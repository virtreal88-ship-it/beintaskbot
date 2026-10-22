"""Tenant-scoped contract shared by the browser adapter and execution pipeline.

Objects must come from server-owned configuration/live expert task authority, never
from model output or client JSON. Validation is not proof that a demo is safe.
"""
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID
import ipaddress

OPERATION = 'inventory.warehouse_transfer'
TOOL = 'beinsystems_demo_browser'
PERMISSIONS = frozenset({'inventory.read', 'inventory.transfer.demo'})
ACTIONS = frozenset({'open_demo', 'observe', 'navigate', 'click', 'fill_demo', 'screenshot'})
TARGETS = frozenset({'inventory', 'transfers', 'new_transfer', 'source_warehouse',
                     'destination_warehouse', 'product', 'quantity', 'save_demo_transfer', 'transfer_result'})
DEMO_VALUES = frozenset({'fixture.source_warehouse', 'fixture.destination_warehouse',
                         'fixture.product', 'fixture.quantity_one'})


class ContractError(ValueError):
    pass


def identity(value):
    try: return str(UUID(str(value)))
    except (ValueError,TypeError,AttributeError): raise ContractError('invalid_identity') from None


@dataclass(frozen=True)
class DemoConnection:
    tenant_id: str
    demo_company_id: str
    connection_ref: str  # opaque vault handle, never credentials or cookies
    origin: str
    account_ref: str
    attestation_ref: str  # owner/security-verified isolation and synthetic-data proof
    restore_receipt_ref: str  # separately verified fixture restore; not an automated reset
    permissions: frozenset
    isolated_session: bool

    def validate(self):
        for value in (self.tenant_id,self.demo_company_id,self.connection_ref,self.account_ref,
                      self.attestation_ref,self.restore_receipt_ref): identity(value)
        parsed=urlsplit(self.origin)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None,443) or parsed.path not in ('','/') or parsed.query or parsed.fragment
            or parsed.hostname.endswith(('.localhost','.local','.internal')) or '.' not in parsed.hostname):
            raise ContractError('invalid_demo_origin')
        host=(parsed.hostname or '').casefold().rstrip('.')
        if host in {'akul.az','www.akul.az','crm.pro.az','www.crm.pro.az'} or any(part in host.split('.') for part in ('prod','production')):
            raise ContractError('production_origin_forbidden')
        try: ipaddress.ip_address(parsed.hostname)
        except ValueError: pass
        else: raise ContractError('ip_origin_forbidden')
        if self.permissions!=PERMISSIONS or self.isolated_session is not True:
            raise ContractError('unsafe_demo_account')
        return self


@dataclass(frozen=True)
class TaskScope:
    tenant_id: str
    task_id: str
    primary_agent_id: str
    expert_agent_id: str
    operation: str
    human_mode: bool
    paused: bool
    group_revision: int | None
    live_group_revision: int | None
    seconds_remaining: int
    actions_remaining: int
    tokens_remaining: int

    def validate(self,connection):
        connection.validate()
        for value in (self.tenant_id,self.task_id,self.primary_agent_id,self.expert_agent_id): identity(value)
        if identity(self.tenant_id)!=identity(connection.tenant_id): raise ContractError('tenant_mismatch')
        if identity(self.primary_agent_id)==identity(self.expert_agent_id) or self.operation!=OPERATION: raise ContractError('invalid_instructor_task')
        if self.human_mode is not False or self.paused is not False: raise ContractError('parent_inactive')
        if self.group_revision is not None or self.live_group_revision is not None:
            if type(self.group_revision) is not int or type(self.live_group_revision) is not int or self.group_revision!=self.live_group_revision:
                raise ContractError('stale_group_revision')
        for value,ceiling in ((self.seconds_remaining,900),(self.actions_remaining,20),(self.tokens_remaining,12000)):
            if type(value) is not int or not 1<=value<=ceiling: raise ContractError('instructor_budget')
        return self


@dataclass(frozen=True)
class BrowserAction:
    action: str
    target: str | None = None
    demo_value: str | None = None

    def validate(self,scope,connection):
        scope.validate(connection)
        if self.action not in ACTIONS: raise ContractError('action_forbidden')
        if self.action in {'navigate','click','fill_demo'}:
            if self.target not in TARGETS: raise ContractError('target_forbidden')
        elif self.target is not None: raise ContractError('unexpected_target')
        if self.action=='fill_demo':
            pairs={'source_warehouse':'fixture.source_warehouse','destination_warehouse':'fixture.destination_warehouse',
                   'product':'fixture.product','quantity':'fixture.quantity_one'}
            if pairs.get(self.target)!=self.demo_value: raise ContractError('demo_value_forbidden')
        elif self.demo_value is not None: raise ContractError('unexpected_demo_value')
        return self


@dataclass(frozen=True)
class ScreenshotEvidence:
    tenant_id: str
    task_id: str
    artifact_id: str
    sha256: str
    sanitization_receipt_ref: str
    ui_version: str
    # No public URLs, image bytes, model-generated images or caller-provided HTML.

    def validate(self,scope):
        for value in (self.tenant_id,self.task_id,self.artifact_id,self.sanitization_receipt_ref): identity(value)
        if identity(self.tenant_id)!=identity(scope.tenant_id) or identity(self.task_id)!=identity(scope.task_id):
            raise ContractError('artifact_scope_mismatch')
        if len(self.sha256)!=64 or any(c not in '0123456789abcdef' for c in self.sha256): raise ContractError('invalid_artifact_hash')
        if not self.ui_version or len(self.ui_version)>120: raise ContractError('missing_ui_version')
        return self


class DemoBrowserAdapter(Protocol):
    """Future server adapter; not implemented or registered in this release.

    Every call must validate live authority, identity banner, destination origin,
    private network/redirect policy and cumulative budgets, not just these shapes.
    """
    async def open_demo(self, connection: DemoConnection, scope: TaskScope, credentials: dict | None = None) -> str: ...
    async def observe(self, session_ref: str, scope: TaskScope) -> dict: ...
    async def act(self, session_ref: str, scope: TaskScope, action: BrowserAction) -> dict: ...
    async def capture(self, session_ref: str, scope: TaskScope) -> ScreenshotEvidence: ...
    async def close(self, session_ref: str) -> None: ...


def availability():
    """Honest capability response: never fabricate a browser run or verified guide."""
    return {'tool':TOOL,'status':'integration_unavailable','operation':OPERATION,
            'verified':False,'screenshots':[],'customer_send_allowed':False,
            'missing':['verified_demo_connection','worker_provider_binding',
                       'private_artifact_storage_configuration','verified_fixture_restore']}
