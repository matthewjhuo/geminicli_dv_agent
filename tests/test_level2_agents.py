"""Level 2 驗證：9 個 DV 子代理定義必須嚴格符合 Gemini CLI 0.68.0 localAgentSchema。

Schema 摘自本機安裝的 packages/core/src/agents/agentLoader.ts（zod `.strict()`）：
kind, name, description, display_name, tools, mcp_servers, model, temperature, max_turns, timeout_mins
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / "project-template" / ".gemini" / "agents"
CONTRACT = ROOT / "project-template" / ".gemini" / "dv" / "contracts" / "agent_output.schema.json"

EXPECTED_AGENTS = [
    "dv-spec", "dv-architect", "dv-rtl", "dv-uvm", "dv-sva",
    "dv-debug", "dv-coverage", "dv-formal", "dv-review",
]
ALLOWED_KEYS = {
    "kind", "name", "description", "display_name", "tools", "mcp_servers",
    "model", "temperature", "max_turns", "timeout_mins",
}
BUILTIN_TOOLS = {
    "read_file", "list_directory", "glob", "grep_search", "write_file",
    "replace", "run_shell_command", "web_fetch", "google_web_search",
    "read_many_files", "save_memory", "write_todos",
}
READ_ONLY_TOOLS = {"read_file", "list_directory", "glob", "grep_search"}
WRITE_TOOLS = {"write_file", "replace"}
# 僅允許「撰寫型」代理取得寫入工具（仍受 Policy ask_user 管制）
WRITE_ALLOWED = {"dv-uvm", "dv-sva"}
NAME_RE = re.compile(r"^[a-z0-9-_]+$")
MCP_SLUG = re.compile(r"^[a-z0-9_.:-]+$", re.I)
FM_RE = re.compile(r"^\ufeff?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n([\s\S]*))?$")


def parse_simple_yaml(raw: str) -> dict:
    """解析本框架使用的 YAML 子集（純量與字串清單），避免引入第三方套件。"""
    data: dict = {}
    current_list = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") or line.startswith("- "):
            if current_list is None:
                raise ValueError(f"list item without key: {line!r}")
            data[current_list].append(line.split("- ", 1)[1].strip())
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val == "":
            data[key] = []
            current_list = key
            continue
        current_list = None
        if re.fullmatch(r"-?\d+", val):
            data[key] = int(val)
        elif re.fullmatch(r"-?\d+\.\d+", val):
            data[key] = float(val)
        else:
            data[key] = val.strip("'\"")
    return data


def is_valid_tool(name: str) -> bool:
    if name in BUILTIN_TOOLS or name == "*":
        return True
    if name.startswith("mcp_"):
        rest = name[4:]
        m = re.match(r"^([^_]+)_(.+)$", rest)
        if not m:
            return False
        server, tool = m.groups()
        return bool(MCP_SLUG.match(server)) and (tool == "*" or bool(MCP_SLUG.match(tool)))
    return False


def load_agent(name: str):
    text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        raise AssertionError(f"{name}: missing frontmatter")
    return parse_simple_yaml(m.group(1)), (m.group(2) or "")


class TestLevel2Agents(unittest.TestCase):
    def test_exact_agent_set(self):
        found = sorted(p.stem for p in AGENTS_DIR.glob("*.md"))
        self.assertEqual(found, sorted(EXPECTED_AGENTS))

    def test_strict_schema(self):
        for name in EXPECTED_AGENTS:
            with self.subTest(agent=name):
                fm, _ = load_agent(name)
                extra = set(fm) - ALLOWED_KEYS
                self.assertFalse(extra, f"unknown keys rejected by .strict(): {extra}")
                self.assertEqual(fm["name"], name)
                self.assertRegex(fm["name"], NAME_RE)
                self.assertEqual(fm.get("kind", "local"), "local")
                self.assertGreaterEqual(len(fm["description"]), 60)
                self.assertIsInstance(fm["max_turns"], int)
                self.assertIsInstance(fm["timeout_mins"], int)
                self.assertTrue(0 < fm["max_turns"] <= 15, "execution limit: max_turns <= 15")
                self.assertTrue(0 < fm["timeout_mins"] <= 8, "execution limit: timeout_mins <= 8")
                self.assertNotIn("model", fm, "不得硬編碼模型名稱，沿用使用者設定")
                for tool in fm["tools"]:
                    self.assertTrue(is_valid_tool(tool), f"invalid tool name {tool}")

    def test_least_privilege_tools(self):
        for name in EXPECTED_AGENTS:
            with self.subTest(agent=name):
                fm, _ = load_agent(name)
                tools = set(fm["tools"])
                self.assertNotIn("run_shell_command", tools, "子代理不得直接取得 shell")
                self.assertNotIn("*", tools)
                self.assertTrue(READ_ONLY_TOOLS <= tools)
                if name not in WRITE_ALLOWED:
                    self.assertFalse(tools & WRITE_TOOLS, f"{name} must be read-only")
        review, _ = load_agent("dv-review")
        self.assertEqual(set(review["tools"]), READ_ONLY_TOOLS, "審查代理必須完全唯讀且無 MCP")

    def test_body_sections_and_contract(self):
        required = ["# Role", "# Input Requirements", "# Security", "# Output Contract", "# Execution Limits"]
        for name in EXPECTED_AGENTS:
            with self.subTest(agent=name):
                _, body = load_agent(name)
                for sec in required:
                    self.assertIn(sec, body)
                self.assertIn("untrusted data", body)
                self.assertIn("agent_output.schema.json", body)
                self.assertIn(f'agent: "{name}"', body)

    def test_contract_schema_is_valid_json(self):
        schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["contract_version"]["const"], "dv-agent-output/v1")
        self.assertIn("review_verdict", schema["properties"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
