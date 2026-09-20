"""reqcompile —— 需求编译（管线第 1 阶段）。

**纯代码，零 LLM 调用。** 这一阶段不花一分钱 token，却决定了后面
所有阶段的靶子对不对：场景名错了，生成的测试标题就对不上；
夹具漏了，功能再好也大面积失败；需求文件读不进来，整条线没有输出。

对外入口：

    from reqcompile import load_requirement_tree, build_scenario_index
    from reqcompile import extract_accessible_names

    tree, report = load_requirement_tree(".../requirements.yaml")
    if not report.ok:
        ...                       # 有 error，别往下走
    index = build_scenario_index(tree)          # → 测试标题靶子
    names = extract_accessible_names(tree)      # → 可访问名必做清单
"""
from .a11y import AccessibleName, AccessibleNameIndex, extract_accessible_names
from .loader import (
    RequirementLoadError,
    ValidationIssue,
    ValidationReport,
    load_requirement_tree,
)
from .model import RequirementNode, RequirementTree, Scenario, ScenarioStep
from .scenarios import ScenarioIndex, ScenarioIndexEntry, build_scenario_index
from .yamlrepair import Repair, RepairResult, load_yaml_lenient, repair_indentation

__all__ = [
    "AccessibleName",
    "AccessibleNameIndex",
    "Repair",
    "RepairResult",
    "RequirementLoadError",
    "RequirementNode",
    "RequirementTree",
    "Scenario",
    "ScenarioIndex",
    "ScenarioIndexEntry",
    "ScenarioStep",
    "ValidationIssue",
    "ValidationReport",
    "build_scenario_index",
    "extract_accessible_names",
    "load_requirement_tree",
    "load_yaml_lenient",
    "repair_indentation",
]
