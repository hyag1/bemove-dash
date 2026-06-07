from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
import unicodedata
from typing import Any, Callable

import pandas as pd
import requests


MONTH_NAMES = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
ACTIVE_MEMBER_STATUSES = {"Ativo", "Suspenso", "VIP"}


class EvoServiceError(RuntimeError):
    pass


class EvoAuthenticationError(EvoServiceError):
    pass


class EvoConnectionError(EvoServiceError):
    pass


class EvoRateLimitError(EvoServiceError):
    pass


class EvoLoadTimeoutError(EvoServiceError):
    pass


ProgressCallback = Callable[[int, int, float], None]
PageCallback = Callable[[int, list[dict], float], None]


@dataclass(frozen=True)
class EvoApiConfig:
    username: str
    password: str
    endpoint: str = "https://evo-integracao-api.w12app.com.br/api/v2/members"
    page_size: int = 50
    max_pages: int = 200
    timeout_seconds: int = 20
    request_interval_seconds: float = 1.55
    rate_limit_retries: int = 1
    max_load_seconds: float = 180.0


@dataclass
class MembershipAnalytics:
    monthly: pd.DataFrame
    plans: pd.DataFrame
    member_statuses: pd.DataFrame
    contract_statuses: pd.DataFrame
    access_health: pd.DataFrame
    branches: pd.DataFrame
    totals: dict[str, int | float]
    fetched_at: datetime
    source: str


@dataclass(frozen=True)
class _PreparedContract:
    member_id: Any
    raw: dict
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    start_period: pd.Period | None
    cancel_period: pd.Period | None


def _extract_records(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("members", "data", "items", "result", "records"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            extracted = _extract_records(nested)
            if extracted:
                return extracted

    return [payload] if "idMember" in payload else []


def fetch_members(
    config: EvoApiConfig,
    progress_callback: ProgressCallback | None = None,
    page_callback: PageCallback | None = None,
) -> list[dict]:
    records: list[dict] = []
    started_at = time.monotonic()
    with requests.Session() as session:
        for page in range(config.max_pages):
            _ensure_load_deadline(config, started_at)
            if page:
                # EVO allows 40 requests/minute per IP. Keep paginated reads below that ceiling.
                time.sleep(config.request_interval_seconds)

            for retry in range(config.rate_limit_retries + 1):
                _ensure_load_deadline(config, started_at)
                try:
                    response = session.get(
                        config.endpoint,
                        auth=(config.username, config.password),
                        params={
                            "showMemberships": "true",
                            "take": config.page_size,
                            "skip": page * config.page_size,
                        },
                        timeout=config.timeout_seconds,
                    )
                except requests.Timeout as exc:
                    raise EvoServiceError("A API da EVO excedeu o tempo limite.") from exc
                except requests.exceptions.ProxyError as exc:
                    raise EvoConnectionError(
                        "O proxy configurado para este processo impediu o acesso a API da EVO. "
                        "Revise as variaveis HTTPS_PROXY e HTTP_PROXY do servidor."
                    ) from exc
                except requests.exceptions.SSLError as exc:
                    raise EvoConnectionError(
                        "A conexao HTTPS com a EVO falhou durante a validacao TLS. "
                        "Verifique certificados, proxy corporativo e antivirus do servidor."
                    ) from exc
                except requests.exceptions.ConnectionError as exc:
                    if "WinError 10013" in str(exc):
                        raise EvoConnectionError(
                            "O processo do dashboard nao possui permissao para acessar a internet via HTTPS. "
                            "Execute o Streamlit em um terminal comum ou libere o Python no firewall, "
                            "antivirus ou politica de rede do servidor."
                        ) from exc
                    raise EvoConnectionError(
                        "Nao foi possivel abrir uma conexao HTTPS com a API da EVO. "
                        "Verifique firewall, antivirus, VPN e proxy do servidor."
                    ) from exc
                except requests.RequestException as exc:
                    raise EvoConnectionError("Nao foi possivel conectar a API da EVO.") from exc

                if response.status_code != 429:
                    break
                if retry >= config.rate_limit_retries:
                    raise EvoRateLimitError(
                        "A EVO limitou temporariamente as consultas. "
                        "Aguarde pelo menos 1 minuto e use Atualizar cache novamente. "
                        "Se o bloqueio persistir, a chave pode ter atingido o limite por hora; "
                        "nesse caso, aguarde 1 hora."
                    )
                time.sleep(_rate_limit_wait_seconds(response))

            _ensure_load_deadline(config, started_at)
            if response.status_code == 401:
                raise EvoAuthenticationError("As credenciais da integracao EVO foram recusadas.")
            if response.status_code != 200:
                raise EvoServiceError(f"A API da EVO respondeu com status {response.status_code}.")

            try:
                batch = _extract_records(response.json())
            except requests.JSONDecodeError as exc:
                raise EvoServiceError("A API da EVO retornou um JSON invalido.") from exc

            records.extend(batch)
            elapsed = time.monotonic() - started_at
            if page_callback is not None:
                page_callback(page + 1, batch, elapsed)
            if progress_callback is not None:
                progress_callback(page + 1, len(records), elapsed)
            if len(batch) < config.page_size:
                break
        else:
            raise EvoServiceError("A consulta atingiu o limite de paginacao configurado.")
    return records


def _ensure_load_deadline(config: EvoApiConfig, started_at: float) -> None:
    if time.monotonic() - started_at <= config.max_load_seconds:
        return
    raise EvoLoadTimeoutError(
        "A EVO nao concluiu a carga dentro do tempo esperado. "
        "O painel interrompeu a consulta para evitar espera indefinida. "
        "Tente Atualizar cache novamente em alguns minutos."
    )


def _rate_limit_wait_seconds(response: requests.Response) -> float:
    try:
        retry_after = float(response.headers.get("Retry-After", ""))
        return min(max(retry_after, 1.0), 60.0)
    except (TypeError, ValueError):
        # A EVO documents a one-minute cooldown for the per-IP minute limit.
        return 60.0


def _timestamp(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.tz_convert(None)


def _period(value: Any) -> pd.Period | None:
    parsed = _timestamp(value)
    return parsed.to_period("M") if parsed is not None else None


def _number_or_none(value: Any) -> float | None:
    try:
        if value in (None, "") or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _period_label(period: pd.Period) -> str:
    return f"{MONTH_NAMES[period.month - 1]}/{str(period.year)[-2:]}"


def _safe_label(value: Any, fallback: str = "Nao informado") -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9 .,_&/()+-]", "", normalized).strip()
    return normalized[:80] or fallback


def _member_status(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in {"active", "ativo"}:
        return "Ativo"
    if normalized in {"inactive", "inativo"}:
        return "Inativo"
    if normalized in {"suspended", "suspenso"}:
        return "Suspenso"
    if normalized == "vip":
        return "VIP"
    return "Outros"


def _contract_status(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in {"active", "ativo"}:
        return "Ativa"
    if normalized in {"inactive", "inativo"}:
        return "Inativa"
    if normalized in {"canceled", "cancelled", "cancelado"}:
        return "Cancelada"
    if normalized in {"expired", "expirado"}:
        return "Expirada"
    if normalized in {"suspended", "suspenso", "frozen"}:
        return "Suspensa"
    return "Outros"


def _contract_end(contract: dict) -> pd.Timestamp | None:
    end_date = _timestamp(contract.get("endDate"))
    if end_date is not None:
        end_date = end_date.normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    cancel_date = _timestamp(contract.get("cancelDate") or contract.get("cancelDateOn"))
    if end_date is None:
        return cancel_date
    if cancel_date is None:
        return end_date
    return min(end_date, cancel_date)


def _contract_live_on(contract: dict, cutoff: pd.Timestamp) -> bool:
    start_date = _timestamp(contract.get("startDate"))
    end_date = _contract_end(contract)
    return start_date is not None and start_date <= cutoff and (end_date is None or end_date >= cutoff)


def _prepare_contract(member_id: Any, contract: dict) -> _PreparedContract:
    start = _timestamp(contract.get("startDate"))
    end = _contract_end(contract)
    cancel = _timestamp(contract.get("cancelDate") or contract.get("cancelDateOn"))
    return _PreparedContract(
        member_id=member_id,
        raw=contract,
        start=start,
        end=end,
        start_period=start.to_period("M") if start is not None else None,
        cancel_period=cancel.to_period("M") if cancel is not None else None,
    )


def _prepared_contract_live_on(contract: _PreparedContract, cutoff: pd.Timestamp) -> bool:
    return contract.start is not None and contract.start <= cutoff and (
        contract.end is None or contract.end >= cutoff
    )


def _access_segment(last_access: Any, reference: pd.Timestamp) -> str:
    access_date = _timestamp(last_access)
    if access_date is None:
        return "Sem acesso registrado"
    days = max((reference.normalize() - access_date.normalize()).days, 0)
    if days <= 7:
        return "Ate 7 dias"
    if days <= 15:
        return "8 a 15 dias"
    if days <= 30:
        return "16 a 30 dias"
    if days <= 60:
        return "31 a 60 dias"
    return "Mais de 60 dias"


def _empty_analytics(*, fetched_at: datetime, source: str) -> MembershipAnalytics:
    return MembershipAnalytics(
        monthly=pd.DataFrame(
            columns=[
                "periodo_date",
                "periodo",
                "novos_clientes",
                "adesoes",
                "renovacoes",
                "cancelamentos",
                "saldo",
                "base_ativa",
            ]
        ),
        plans=pd.DataFrame(columns=["plano", "matriculas_ativas"]),
        member_statuses=pd.DataFrame(columns=["status", "membros"]),
        contract_statuses=pd.DataFrame(columns=["status", "contratos"]),
        access_health=pd.DataFrame(columns=["faixa", "membros"]),
        branches=pd.DataFrame(columns=["unidade", "membros_ativos"]),
        totals={},
        fetched_at=fetched_at,
        source=source,
    )


def build_membership_analytics(
    records: list[dict],
    *,
    fetched_at: datetime | None = None,
    source: str = "API EVO - Members",
) -> MembershipAnalytics:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    reference = pd.Timestamp(fetched_at).tz_convert(None) if pd.Timestamp(fetched_at).tzinfo else pd.Timestamp(fetched_at)
    members = {
        record.get("idMember"): record
        for record in records
        if isinstance(record, dict) and record.get("idMember") is not None
    }
    if not members:
        return _empty_analytics(fetched_at=fetched_at, source=source)

    contracts_by_member: defaultdict[Any, list[_PreparedContract]] = defaultdict(list)
    all_contracts: list[_PreparedContract] = []
    for member_id, member in members.items():
        for contract in member.get("memberships") or []:
            if isinstance(contract, dict):
                prepared = _prepare_contract(member_id, contract)
                contracts_by_member[member_id].append(prepared)
                all_contracts.append(prepared)

    member_categories = {member_id: _member_status(member.get("status")) for member_id, member in members.items()}
    active_member_ids = {
        member_id for member_id, status in member_categories.items() if status in ACTIVE_MEMBER_STATUSES
    }

    current_contracts: list[_PreparedContract] = []
    latest_contracts: list[_PreparedContract] = []
    for member_id, contracts in contracts_by_member.items():
        latest = max(
            contracts,
            key=lambda contract: contract.start or pd.Timestamp.min,
        )
        latest_contracts.append(latest)
        current_contracts.extend(contract for contract in contracts if _prepared_contract_live_on(contract, reference))

    first_membership_period: dict[Any, pd.Period] = {}
    for member_id, contracts in contracts_by_member.items():
        starts = [contract.start_period for contract in contracts]
        valid_starts = [period for period in starts if period is not None]
        if valid_starts:
            first_membership_period[member_id] = min(valid_starts)

    periods = {
        period
        for contract in all_contracts
        for period in (contract.start_period, contract.cancel_period)
        if period is not None and period <= reference.to_period("M")
    }
    periods.update(period for period in first_membership_period.values() if period <= reference.to_period("M"))
    if not periods:
        periods = {reference.to_period("M")}
    period_range = pd.period_range(min(periods), reference.to_period("M"), freq="M")
    new_members_by_period: defaultdict[pd.Period, int] = defaultdict(int)
    for first_period in first_membership_period.values():
        new_members_by_period[first_period] += 1
    adhesions_by_period: defaultdict[pd.Period, int] = defaultdict(int)
    renewals_by_period: defaultdict[pd.Period, int] = defaultdict(int)
    canceled_member_ids_by_period: defaultdict[pd.Period, set[Any]] = defaultdict(set)
    for contract in all_contracts:
        if contract.start_period is not None:
            if contract.raw.get("idMemberMembershipRenewed") is None:
                adhesions_by_period[contract.start_period] += 1
            else:
                renewals_by_period[contract.start_period] += 1
        if contract.cancel_period is not None:
            canceled_member_ids_by_period[contract.cancel_period].add(contract.member_id)

    monthly_rows = []
    for period in period_range:
        month_cutoff = min(period.end_time, reference) if period == reference.to_period("M") else period.end_time
        active_at_end = {
            member_id
            for member_id, contracts in contracts_by_member.items()
            if any(_prepared_contract_live_on(contract, month_cutoff) for contract in contracts)
        }
        new_members = new_members_by_period[period]
        adhesions = adhesions_by_period[period]
        renewals = renewals_by_period[period]
        canceled_member_ids = canceled_member_ids_by_period[period]
        cancellations = len(canceled_member_ids)
        monthly_rows.append(
            {
                "periodo_date": period.to_timestamp(),
                "periodo": _period_label(period),
                "novos_clientes": new_members,
                "adesoes": adhesions,
                "renovacoes": renewals,
                "cancelamentos": cancellations,
                "saldo": new_members - cancellations,
                "base_ativa": len(active_at_end),
            }
        )

    plan_counts: defaultdict[str, int] = defaultdict(int)
    next_month_value = 0.0
    next_month_value_contracts = 0
    terms_pending = 0
    for contract in current_contracts:
        member_id = contract.member_id
        raw_contract = contract.raw
        if member_id not in active_member_ids:
            continue
        plan_counts[_safe_label(raw_contract.get("name"))] += 1
        value_next_month = _number_or_none(raw_contract.get("valueNextMonth"))
        if value_next_month is not None:
            next_month_value += value_next_month
            next_month_value_contracts += 1
        if raw_contract.get("signedTerms") is False:
            terms_pending += 1

    member_status_counts: defaultdict[str, int] = defaultdict(int)
    branch_counts: defaultdict[str, int] = defaultdict(int)
    access_counts: defaultdict[str, int] = defaultdict(int)
    for member_id, member in members.items():
        member_status_counts[member_categories[member_id]] += 1
        if member_id in active_member_ids:
            branch_counts[_safe_label(member.get("branchName"), "Unidade nao informada")] += 1
            access_counts[_access_segment(member.get("lastAccessDate"), reference)] += 1

    contract_status_counts: defaultdict[str, int] = defaultdict(int)
    for contract in latest_contracts:
        contract_status_counts[_contract_status(contract.raw.get("membershipStatus"))] += 1

    risk_members = sum(
        access_counts[label] for label in ("31 a 60 dias", "Mais de 60 dias", "Sem acesso registrado")
    )
    totals: dict[str, int | float] = {
        "membros": len(members),
        "membros_ativos": len(active_member_ids),
        "membros_inativos": member_status_counts["Inativo"],
        "risco_evasao": risk_members,
        "acessos_bloqueados": sum(
            1 for member_id, member in members.items() if member_id in active_member_ids and member.get("accessBlocked") is True
        ),
        "matriculas_ativas": sum(plan_counts.values()),
        "termos_pendentes": terms_pending,
        "valor_proximo_mes": round(next_month_value, 2),
        "contratos_com_valor_proximo_mes": next_month_value_contracts,
    }
    return MembershipAnalytics(
        monthly=pd.DataFrame(monthly_rows),
        plans=pd.DataFrame(
            [{"plano": plan, "matriculas_ativas": count} for plan, count in sorted(plan_counts.items())]
        ),
        member_statuses=pd.DataFrame(
            [{"status": status, "membros": count} for status, count in sorted(member_status_counts.items())]
        ),
        contract_statuses=pd.DataFrame(
            [{"status": status, "contratos": count} for status, count in sorted(contract_status_counts.items())]
        ),
        access_health=pd.DataFrame(
            [{"faixa": label, "membros": access_counts[label]} for label in (
                "Ate 7 dias",
                "8 a 15 dias",
                "16 a 30 dias",
                "31 a 60 dias",
                "Mais de 60 dias",
                "Sem acesso registrado",
            ) if access_counts[label]]
        ),
        branches=pd.DataFrame(
            [{"unidade": branch, "membros_ativos": count} for branch, count in sorted(branch_counts.items())]
        ),
        totals=totals,
        fetched_at=fetched_at,
        source=source,
    )


def load_membership_analytics(
    config: EvoApiConfig,
    progress_callback: ProgressCallback | None = None,
) -> MembershipAnalytics:
    return build_membership_analytics(fetch_members(config, progress_callback))


def demo_membership_analytics() -> MembershipAnalytics:
    months = pd.period_range("2025-07", "2026-06", freq="M")
    new_members = [48, 55, 51, 62, 59, 65, 57, 61, 70, 73, 68, 72]
    adhesions = [54, 60, 57, 68, 64, 70, 62, 67, 76, 79, 75, 78]
    renewals = [31, 34, 38, 35, 42, 45, 43, 48, 51, 53, 56, 59]
    cancellations = [29, 31, 27, 36, 34, 39, 42, 37, 35, 44, 41, 38]
    base = 510
    monthly_rows = []
    for index, period in enumerate(months):
        base += new_members[index] - cancellations[index]
        monthly_rows.append(
            {
                "periodo_date": period.to_timestamp(),
                "periodo": _period_label(period),
                "novos_clientes": new_members[index],
                "adesoes": adhesions[index],
                "renovacoes": renewals[index],
                "cancelamentos": cancellations[index],
                "saldo": new_members[index] - cancellations[index],
                "base_ativa": base,
            }
        )
    return MembershipAnalytics(
        monthly=pd.DataFrame(monthly_rows),
        plans=pd.DataFrame(
            [
                {"plano": "CLUB 12", "matriculas_ativas": 183},
                {"plano": "CLUB 16", "matriculas_ativas": 211},
                {"plano": "CLUB 20", "matriculas_ativas": 161},
                {"plano": "Outros", "matriculas_ativas": 82},
            ]
        ),
        member_statuses=pd.DataFrame(
            [{"status": "Ativo", "membros": base}, {"status": "Inativo", "membros": 284}]
        ),
        contract_statuses=pd.DataFrame(
            [
                {"status": "Ativa", "contratos": 637},
                {"status": "Cancelada", "contratos": 194},
                {"status": "Expirada", "contratos": 90},
            ]
        ),
        access_health=pd.DataFrame(
            [
                {"faixa": "Ate 7 dias", "membros": 314},
                {"faixa": "8 a 15 dias", "membros": 126},
                {"faixa": "16 a 30 dias", "membros": 83},
                {"faixa": "31 a 60 dias", "membros": 61},
                {"faixa": "Mais de 60 dias", "membros": 38},
                {"faixa": "Sem acesso registrado", "membros": 15},
            ]
        ),
        branches=pd.DataFrame([{"unidade": "BE.MOVE Cidade Jardim", "membros_ativos": base}]),
        totals={
            "membros": base + 284,
            "membros_ativos": base,
            "membros_inativos": 284,
            "risco_evasao": 114,
            "acessos_bloqueados": 11,
            "matriculas_ativas": 637,
            "termos_pendentes": 8,
            "valor_proximo_mes": 153_480.0,
            "contratos_com_valor_proximo_mes": 612,
        },
        fetched_at=datetime.now(timezone.utc),
        source="Dados demonstrativos",
    )
