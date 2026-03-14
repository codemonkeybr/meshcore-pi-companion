"""REST API for fanout config CRUD."""

import ast
import inspect
import logging
import re
import string

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings as server_settings
from app.fanout.bot_exec import _analyze_bot_signature
from app.repository.fanout import FanoutConfigRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fanout", tags=["fanout"])

_VALID_TYPES = {"mqtt_private", "mqtt_community", "bot", "webhook", "apprise", "sqs"}

_IATA_RE = re.compile(r"^[A-Z]{3}$")
_DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE = "meshcore/{IATA}/{PUBLIC_KEY}/packets"
_DEFAULT_COMMUNITY_MQTT_BROKER_HOST = "mqtt-us-v1.letsmesh.net"
_DEFAULT_COMMUNITY_MQTT_BROKER_PORT = 443
_DEFAULT_COMMUNITY_MQTT_TRANSPORT = "websockets"
_DEFAULT_COMMUNITY_MQTT_AUTH_MODE = "token"
_COMMUNITY_MQTT_TEMPLATE_FIELD_CANONICAL = {
    "iata": "IATA",
    "public_key": "PUBLIC_KEY",
}
_ALLOWED_COMMUNITY_MQTT_TRANSPORTS = {"tcp", "websockets"}
_ALLOWED_COMMUNITY_MQTT_AUTH_MODES = {"token", "password", "none"}


def _normalize_community_topic_template(topic_template: str) -> str:
    """Normalize Community MQTT topic template placeholders to canonical uppercase form."""
    template = topic_template.strip() or _DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE
    parts: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for literal_text, field_name, format_spec, conversion in parsed:
            parts.append(literal_text)
            if field_name is None:
                continue
            normalized_field = _COMMUNITY_MQTT_TEMPLATE_FIELD_CANONICAL.get(field_name.lower())
            if normalized_field is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"topic_template may only use {{IATA}} and {{PUBLIC_KEY}}; got {field_name}"
                    ),
                )
            replacement = ["{", normalized_field]
            if conversion:
                replacement.extend(["!", conversion])
            if format_spec:
                replacement.extend([":", format_spec])
            replacement.append("}")
            parts.append("".join(replacement))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid topic_template: {exc}") from None

    return "".join(parts)


class FanoutConfigCreate(BaseModel):
    type: str = Field(description="Integration type: 'mqtt_private' or 'mqtt_community'")
    name: str = Field(min_length=1, description="User-assigned label")
    config: dict = Field(default_factory=dict, description="Type-specific config blob")
    scope: dict = Field(default_factory=dict, description="Scope controls")
    enabled: bool = Field(default=True, description="Whether enabled on creation")


class FanoutConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, description="Updated label")
    config: dict | None = Field(default=None, description="Updated config blob")
    scope: dict | None = Field(default=None, description="Updated scope controls")
    enabled: bool | None = Field(default=None, description="Enable/disable toggle")


def _validate_mqtt_private_config(config: dict) -> None:
    """Validate mqtt_private config blob."""
    if not config.get("broker_host"):
        raise HTTPException(status_code=400, detail="broker_host is required for mqtt_private")
    port = config.get("broker_port", 1883)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="broker_port must be between 1 and 65535")


def _validate_mqtt_community_config(config: dict) -> None:
    """Validate mqtt_community config blob. Normalizes IATA to uppercase."""
    broker_host = str(config.get("broker_host", _DEFAULT_COMMUNITY_MQTT_BROKER_HOST)).strip()
    if not broker_host:
        broker_host = _DEFAULT_COMMUNITY_MQTT_BROKER_HOST
    config["broker_host"] = broker_host

    port = config.get("broker_port", _DEFAULT_COMMUNITY_MQTT_BROKER_PORT)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="broker_port must be between 1 and 65535")
    config["broker_port"] = port

    transport = str(config.get("transport", _DEFAULT_COMMUNITY_MQTT_TRANSPORT)).strip().lower()
    if transport not in _ALLOWED_COMMUNITY_MQTT_TRANSPORTS:
        raise HTTPException(
            status_code=400,
            detail="transport must be 'websockets' or 'tcp'",
        )
    config["transport"] = transport
    config["use_tls"] = bool(config.get("use_tls", True))
    config["tls_verify"] = bool(config.get("tls_verify", True))

    auth_mode = str(config.get("auth_mode", _DEFAULT_COMMUNITY_MQTT_AUTH_MODE)).strip().lower()
    if auth_mode not in _ALLOWED_COMMUNITY_MQTT_AUTH_MODES:
        raise HTTPException(
            status_code=400,
            detail="auth_mode must be 'token', 'password', or 'none'",
        )
    config["auth_mode"] = auth_mode
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if auth_mode == "password" and (not username or not password):
        raise HTTPException(
            status_code=400,
            detail="username and password are required when auth_mode is 'password'",
        )
    config["username"] = username
    config["password"] = password

    token_audience = str(config.get("token_audience", "")).strip()
    config["token_audience"] = token_audience

    iata = config.get("iata", "").upper().strip()
    if not iata or not _IATA_RE.fullmatch(iata):
        raise HTTPException(
            status_code=400,
            detail="IATA code is required and must be exactly 3 uppercase alphabetic characters",
        )
    config["iata"] = iata

    topic_template = str(
        config.get("topic_template", _DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE)
    ).strip()
    if not topic_template:
        topic_template = _DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE

    config["topic_template"] = _normalize_community_topic_template(topic_template)


def _validate_bot_config(config: dict) -> None:
    """Validate bot config blob (syntax-check the code and supported signature)."""
    code = config.get("code", "")
    if not code or not code.strip():
        raise HTTPException(status_code=400, detail="Bot code cannot be empty")
    try:
        tree = ast.parse(code, filename="<bot_code>", mode="exec")
    except SyntaxError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Bot code has syntax error at line {e.lineno}: {e.msg}",
        ) from None

    bot_def = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "bot"
        ),
        None,
    )
    if bot_def is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Bot code must define a callable bot() function. "
                "Use the default bot template as a reference."
            ),
        )

    try:
        parameters: list[inspect.Parameter] = []
        positional_args = [
            *((arg, inspect.Parameter.POSITIONAL_ONLY) for arg in bot_def.args.posonlyargs),
            *((arg, inspect.Parameter.POSITIONAL_OR_KEYWORD) for arg in bot_def.args.args),
        ]
        positional_defaults_start = len(positional_args) - len(bot_def.args.defaults)
        sentinel_default = object()

        for index, (arg, kind) in enumerate(positional_args):
            has_default = index >= positional_defaults_start
            parameters.append(
                inspect.Parameter(
                    arg.arg,
                    kind=kind,
                    default=sentinel_default if has_default else inspect.Parameter.empty,
                )
            )
        if bot_def.args.vararg is not None:
            parameters.append(
                inspect.Parameter(bot_def.args.vararg.arg, kind=inspect.Parameter.VAR_POSITIONAL)
            )
        for kwonly_arg, kw_default in zip(
            bot_def.args.kwonlyargs, bot_def.args.kw_defaults, strict=True
        ):
            parameters.append(
                inspect.Parameter(
                    kwonly_arg.arg,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=(
                        sentinel_default if kw_default is not None else inspect.Parameter.empty
                    ),
                )
            )
        if bot_def.args.kwarg is not None:
            parameters.append(
                inspect.Parameter(bot_def.args.kwarg.arg, kind=inspect.Parameter.VAR_KEYWORD)
            )

        _analyze_bot_signature(inspect.Signature(parameters))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _validate_apprise_config(config: dict) -> None:
    """Validate apprise config blob."""
    urls = config.get("urls", "")
    if not urls or not urls.strip():
        raise HTTPException(status_code=400, detail="At least one Apprise URL is required")


def _validate_webhook_config(config: dict) -> None:
    """Validate webhook config blob."""
    url = config.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="url is required for webhook")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    method = config.get("method", "POST").upper()
    if method not in ("POST", "PUT", "PATCH"):
        raise HTTPException(status_code=400, detail="method must be POST, PUT, or PATCH")
    headers = config.get("headers", {})
    if not isinstance(headers, dict):
        raise HTTPException(status_code=400, detail="headers must be a JSON object")


def _validate_sqs_config(config: dict) -> None:
    """Validate sqs config blob."""
    queue_url = str(config.get("queue_url", "")).strip()
    if not queue_url:
        raise HTTPException(status_code=400, detail="queue_url is required for sqs")
    if not queue_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="queue_url must start with http:// or https://")

    endpoint_url = str(config.get("endpoint_url", "")).strip()
    if endpoint_url and not endpoint_url.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=400,
            detail="endpoint_url must start with http:// or https://",
        )

    access_key_id = str(config.get("access_key_id", "")).strip()
    secret_access_key = str(config.get("secret_access_key", "")).strip()
    session_token = str(config.get("session_token", "")).strip()
    has_static_keypair = bool(access_key_id) and bool(secret_access_key)
    has_partial_keypair = bool(access_key_id) != bool(secret_access_key)

    if has_partial_keypair:
        raise HTTPException(
            status_code=400,
            detail="access_key_id and secret_access_key must be set together for sqs",
        )
    if session_token and not has_static_keypair:
        raise HTTPException(
            status_code=400,
            detail="session_token requires access_key_id and secret_access_key for sqs",
        )


def _enforce_scope(config_type: str, scope: dict) -> dict:
    """Enforce type-specific scope constraints. Returns normalized scope."""
    if config_type == "mqtt_community":
        return {"messages": "none", "raw_packets": "all"}
    if config_type == "bot":
        return {"messages": "all", "raw_packets": "none"}
    if config_type in ("webhook", "apprise"):
        messages = scope.get("messages", "all")
        if messages not in ("all", "none") and not isinstance(messages, dict):
            raise HTTPException(
                status_code=400,
                detail="scope.messages must be 'all', 'none', or a filter object",
            )
        return {"messages": messages, "raw_packets": "none"}
    # For mqtt_private and sqs, validate scope values
    messages = scope.get("messages", "all")
    if messages not in ("all", "none") and not isinstance(messages, dict):
        raise HTTPException(
            status_code=400,
            detail="scope.messages must be 'all', 'none', or a filter object",
        )
    raw_packets = scope.get("raw_packets", "all")
    if raw_packets not in ("all", "none"):
        raise HTTPException(
            status_code=400,
            detail="scope.raw_packets must be 'all' or 'none'",
        )
    return {"messages": messages, "raw_packets": raw_packets}


@router.get("")
async def list_fanout_configs() -> list[dict]:
    """List all fanout configs."""
    return await FanoutConfigRepository.get_all()


@router.post("")
async def create_fanout_config(body: FanoutConfigCreate) -> dict:
    """Create a new fanout config."""
    if body.type not in _VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{body.type}'. Must be one of: {', '.join(sorted(_VALID_TYPES))}",
        )

    if body.type == "bot" and server_settings.disable_bots:
        raise HTTPException(status_code=403, detail="Bot system disabled by server configuration")

    # Only validate config when creating as enabled — disabled configs
    # are drafts the user hasn't finished configuring yet.
    if body.enabled:
        if body.type == "mqtt_private":
            _validate_mqtt_private_config(body.config)
        elif body.type == "mqtt_community":
            _validate_mqtt_community_config(body.config)
        elif body.type == "bot":
            _validate_bot_config(body.config)
        elif body.type == "webhook":
            _validate_webhook_config(body.config)
        elif body.type == "apprise":
            _validate_apprise_config(body.config)
        elif body.type == "sqs":
            _validate_sqs_config(body.config)

    scope = _enforce_scope(body.type, body.scope)

    cfg = await FanoutConfigRepository.create(
        config_type=body.type,
        name=body.name,
        config=body.config,
        scope=scope,
        enabled=body.enabled,
    )

    # Start the module if enabled
    if cfg["enabled"]:
        from app.fanout.manager import fanout_manager

        await fanout_manager.reload_config(cfg["id"])

    logger.info("Created fanout config %s (type=%s, name=%s)", cfg["id"], body.type, body.name)
    return cfg


@router.patch("/{config_id}")
async def update_fanout_config(config_id: str, body: FanoutConfigUpdate) -> dict:
    """Update a fanout config. Triggers module reload."""
    existing = await FanoutConfigRepository.get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")

    if existing["type"] == "bot" and server_settings.disable_bots:
        raise HTTPException(status_code=403, detail="Bot system disabled by server configuration")

    kwargs = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.enabled is not None:
        kwargs["enabled"] = body.enabled
    if body.config is not None:
        kwargs["config"] = body.config
    if body.scope is not None:
        kwargs["scope"] = _enforce_scope(existing["type"], body.scope)

    # Validate config when the result will be enabled
    will_be_enabled = body.enabled if body.enabled is not None else existing["enabled"]
    if will_be_enabled:
        config_to_validate = body.config if body.config is not None else existing["config"]
        if existing["type"] == "mqtt_private":
            _validate_mqtt_private_config(config_to_validate)
        elif existing["type"] == "mqtt_community":
            _validate_mqtt_community_config(config_to_validate)
        elif existing["type"] == "bot":
            _validate_bot_config(config_to_validate)
        elif existing["type"] == "webhook":
            _validate_webhook_config(config_to_validate)
        elif existing["type"] == "apprise":
            _validate_apprise_config(config_to_validate)
        elif existing["type"] == "sqs":
            _validate_sqs_config(config_to_validate)

    updated = await FanoutConfigRepository.update(config_id, **kwargs)
    if updated is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")

    # Reload the module to pick up changes
    from app.fanout.manager import fanout_manager

    await fanout_manager.reload_config(config_id)

    logger.info("Updated fanout config %s", config_id)
    return updated


@router.delete("/{config_id}")
async def delete_fanout_config(config_id: str) -> dict:
    """Delete a fanout config."""
    existing = await FanoutConfigRepository.get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")

    # Stop the module first
    from app.fanout.manager import fanout_manager

    await fanout_manager.remove_config(config_id)
    await FanoutConfigRepository.delete(config_id)

    logger.info("Deleted fanout config %s", config_id)
    return {"deleted": True}
