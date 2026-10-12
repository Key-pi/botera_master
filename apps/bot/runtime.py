from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured, ValidationError

from apps.bot.selectors import active_polling_bot_instances
from apps.bot.services import decrypt_bot_token
from apps.partners.setup import get_partner_setup_status


@dataclass(frozen=True, slots=True)
class RuntimeBotConfig:
    bot_instance_id: UUID
    partner_id: UUID
    token: str
    token_masked: str
    partner_timezone: str
    partner_language: str


@dataclass(frozen=True, slots=True)
class SkippedBotInstance:
    bot_instance_id: UUID
    partner_id: UUID
    reason_code: str
    detail: str


@dataclass(frozen=True, slots=True)
class RuntimeLoadResult:
    configs: tuple[RuntimeBotConfig, ...]
    skipped: tuple[SkippedBotInstance, ...]


def load_runtime_bot_configs(
    *,
    completed_setup_steps_by_partner: Mapping[UUID, set[str]] | None = None,
) -> RuntimeLoadResult:
    """Build polling runtime configs without network I/O.

    Later milestones will replace the optional setup-step injection with real
    catalog/schedule/bot-settings checkers as those apps gain persistent models.
    """

    completed_setup_steps_by_partner = completed_setup_steps_by_partner or {}
    configs: list[RuntimeBotConfig] = []
    skipped: list[SkippedBotInstance] = []
    seen_fingerprints: set[str] = set()

    for bot_instance in active_polling_bot_instances():
        partner = bot_instance.partner
        extra_steps = completed_setup_steps_by_partner.get(partner.id, set())
        setup_status = get_partner_setup_status(
            partner,
            completed_external_steps=extra_steps,
        )
        if not setup_status.is_complete or partner.status != partner.Status.ACTIVE:
            skipped.append(
                SkippedBotInstance(
                    bot_instance_id=bot_instance.id,
                    partner_id=partner.id,
                    reason_code="partner_not_runtime_eligible",
                    detail=",".join(setup_status.missing_steps),
                )
            )
            continue

        if not bot_instance.has_token:
            skipped.append(
                SkippedBotInstance(
                    bot_instance_id=bot_instance.id,
                    partner_id=partner.id,
                    reason_code="bot_token_missing",
                    detail="Bot token is missing.",
                )
            )
            continue

        if bot_instance.token_fingerprint in seen_fingerprints:
            skipped.append(
                SkippedBotInstance(
                    bot_instance_id=bot_instance.id,
                    partner_id=partner.id,
                    reason_code="duplicate_token",
                    detail="Duplicate token fingerprint in runtime selection.",
                )
            )
            continue
        seen_fingerprints.add(bot_instance.token_fingerprint or "")

        try:
            token = decrypt_bot_token(bot_instance)
        except (ImproperlyConfigured, ValidationError) as exc:
            skipped.append(
                SkippedBotInstance(
                    bot_instance_id=bot_instance.id,
                    partner_id=partner.id,
                    reason_code="token_decrypt_failed",
                    detail=str(exc),
                )
            )
            continue

        configs.append(
            RuntimeBotConfig(
                bot_instance_id=bot_instance.id,
                partner_id=partner.id,
                token=token,
                token_masked=bot_instance.token_masked,
                partner_timezone=partner.timezone,
                partner_language=partner.default_language,
            )
        )

    return RuntimeLoadResult(configs=tuple(configs), skipped=tuple(skipped))
