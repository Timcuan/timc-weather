from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from config import (
    CLOB_BASE_URL,
    ENABLE_ADVANCED_ANTI_BLOCK,
    GAMMA_BASE_URL,
    LLM_API_KEY,
    LLM_PROVIDER,
    OPEN_METEO_ENSEMBLE_URL,
    PAPER_TRADING,
    PRIVATE_KEY,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
)


@dataclass
class RuntimeNeedsReport:
    recommendation: str
    missing: list[str]
    warnings: list[str]
    checks: dict[str, bool]


class RuntimeNeedsEvaluator:
    def __init__(self) -> None:
        self.required_hosts = self._build_required_hosts()

    def evaluate(self) -> RuntimeNeedsReport:
        missing: list[str] = []
        warnings: list[str] = []
        checks: dict[str, bool] = {}

        checks["telegram_token"] = bool(TELEGRAM_TOKEN and not TELEGRAM_TOKEN.startswith("replace_with_"))
        checks["telegram_chat_id"] = bool(TELEGRAM_CHAT_ID and not TELEGRAM_CHAT_ID.startswith("replace_with_"))
        checks["llm_provider"] = bool(LLM_PROVIDER)
        checks["llm_api_key"] = bool(LLM_API_KEY) if LLM_PROVIDER == "gemini" else True
        checks["private_key"] = bool(PRIVATE_KEY)
        checks["network_dns"] = self._check_dns_hosts()

        if not checks["telegram_token"]:
            missing.append("TELEGRAM_TOKEN")
        if not checks["telegram_chat_id"]:
            missing.append("TELEGRAM_CHAT_ID")
        if LLM_PROVIDER == "gemini" and not checks["llm_api_key"]:
            missing.append("LLM_API_KEY")
        if not PAPER_TRADING and not checks["private_key"]:
            missing.append("PRIVATE_KEY")
        if not checks["network_dns"]:
            warnings.append("DNS/network unresolved for one or more required endpoints")

        if not checks["network_dns"]:
            recommendation = "OFFLINE_SAFE"
        elif not PAPER_TRADING and not checks["private_key"]:
            recommendation = "PAPER_ONLY"
        elif missing:
            recommendation = "PAPER_ONLY"
        else:
            recommendation = "LIVE_OK" if not PAPER_TRADING else "PAPER_OK"

        if ENABLE_ADVANCED_ANTI_BLOCK:
            warnings.append("Advanced anti-block enabled: ensure request latency and proxy quality are monitored")

        return RuntimeNeedsReport(
            recommendation=recommendation,
            missing=missing,
            warnings=warnings,
            checks=checks,
        )

    def _build_required_hosts(self) -> list[str]:
        urls = [GAMMA_BASE_URL, CLOB_BASE_URL, OPEN_METEO_ENSEMBLE_URL, "https://api.telegram.org"]
        if LLM_PROVIDER == "gemini":
            urls.append("https://generativelanguage.googleapis.com")
        hosts: list[str] = []
        for url in urls:
            host = urlparse(url).hostname
            if host:
                hosts.append(host)
        return sorted(set(hosts))

    def _check_dns_hosts(self) -> bool:
        for host in self.required_hosts:
            try:
                socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            except OSError:
                return False
        return True

    def to_text(self, report: RuntimeNeedsReport) -> str:
        lines: list[str] = [
            f"recommendation={report.recommendation}",
            "checks:",
        ]
        for key, ok in sorted(report.checks.items()):
            lines.append(f"- {key}: {'ok' if ok else 'fail'}")
        if report.missing:
            lines.append("missing:")
            for item in report.missing:
                lines.append(f"- {item}")
        if report.warnings:
            lines.append("warnings:")
            for item in report.warnings:
                lines.append(f"- {item}")
        return "\n".join(lines)

