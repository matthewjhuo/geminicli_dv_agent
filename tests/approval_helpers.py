"""Test-only trusted approval records; does not invoke a model or external service."""
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from dv_agent_platform.config import FrameworkConfig
from dv_agent_platform.security.approval import ApprovalGate, ApprovalFileApprover


def grant_memory(case, root_or_store, entry_id, project_id, approver, approval_id):
    root = getattr(root_or_store, 'project_root', root_or_store)
    project_id = project_id or FrameworkConfig.load(project_root=root).project_id
    temporary = tempfile.TemporaryDirectory(prefix='dv-trusted-approval-')
    case.addCleanup(temporary.cleanup)
    path = Path(temporary.name) / 'approval.json'
    path.write_text(json.dumps({'approvals': [{
        'approval_id': approval_id, 'category': 'memory_promotion', 'action': 'confirm_memory',
        'approver': approver, 'expires_at': time.time()+3600,
        'scope': {'entry_id': entry_id, 'project_id': project_id},
    }]}), encoding='utf-8')
    previous = os.environ.get('DV_APPROVAL_FILE')
    os.environ['DV_APPROVAL_FILE'] = str(path)
    def restore():
        if previous is None:
            os.environ.pop('DV_APPROVAL_FILE', None)
        else:
            os.environ['DV_APPROVAL_FILE'] = previous
    case.addCleanup(restore)
    if hasattr(root_or_store, 'approval_gate'):
        root_or_store.approval_gate = ApprovalGate(ApprovalFileApprover(path))
    return path
