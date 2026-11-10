"""Stable snapshots and independent agent trees for session conflict recovery."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from src.engine.agents.tree.tree import AgentTree
from src.engine.storage.executions import root_id_for


def fingerprint(messages: list[ModelMessage], tree: AgentTree | None, attributes: Mapping[str, object]) -> str:
    tree_data = None if tree is None else tree.to_dict()
    if tree_data is not None:
        for node in tree_data['nodes']:
            if node['parent_id'] is None:
                node.pop('messages', None)
    payload = {
        **attributes,
        'messages': ModelMessagesTypeAdapter.dump_python(messages, mode='json'),
        'tree': tree_data,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def copy_tree(tree: AgentTree | None, session_id: str) -> AgentTree | None:
    if tree is None:
        return None
    data = tree.to_dict()
    ids = {
        node['id']: root_id_for(session_id)
        if node['parent_id'] is None
        else uuid.uuid5(uuid.NAMESPACE_URL, f'{session_id}:{node["id"]}').hex
        for node in data['nodes']
    }
    for node in data['nodes']:
        node['id'] = ids[node['id']]
        node['parent_id'] = ids.get(node['parent_id'])
        node['channel']['siblings'] = [ids.get(value, value) for value in node['channel']['siblings']]
        if node['status'] == 'running':
            node['status'] = 'cancelled'
    for entry in data['notifications']:
        entry['sender_id'] = ids.get(entry['sender_id'], entry['sender_id'])
        entry['receiver_id'] = ids.get(entry['receiver_id'], entry['receiver_id'])
    return AgentTree.from_dict(data)
