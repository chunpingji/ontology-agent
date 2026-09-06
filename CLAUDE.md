# CLAUDE.md
This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/019-evidence-semantic-extraction/plan.md
<!-- SPECKIT END -->


## Core Instruction for Codex CLI Collaboration

在任何时刻，你必须思考当前过程可以如何与 Codex 进行协作，通过 `codex exec` / `codex review` CLI 命令调用 Codex 作为你客观全面分析的保障。
其中你**务必执行**以下几个步骤：
**1** 在你对用户需求形成初步分析后，将用户需求、初始思路告知 Codex，并要求其完善需求分析和实施计划。
**2** 无论何时，只要完成切实编码行为后，**必须立即使用 `codex review` 审查代码改动和对应需求完成程度**。
**3** Codex 只能给出参考，你**必须有自己的思考，甚至需要对 Codex 的回答提出质疑**。尽信书则不如无书，你与 Codex 的最终使命都是达成统一、全面、精准的意见，所以你们必须不断争辩以找到通向真理的唯一途径。

## Codex CLI Invocation Specification

### 1. 工具概述

Codex 通过 CLI 命令行调用，主要使用两个子命令：
- `codex exec` — 非交互式执行分析/原型任务（read-only 沙箱）
- `codex review` — 代码审查（review uncommitted 改动或对比 base branch）

### 2. 调用模板

**分析 & 原型（read-only，严禁修改文件）：**
```bash
codex exec --sandbox read-only -C "<项目根目录绝对路径>" -o /tmp/codex-output.md "<明确的任务指令>"
```

**代码审查（review 未提交的改动）：**
```bash
codex review --uncommitted -C "<项目根目录绝对路径>" "<审查要求>"
```

**代码审查（对比 base branch）：**
```bash
codex review --base main -C "<项目根目录绝对路径>" "<审查要求>"
```

### 3. 关键规则

- **必须** 使用 `--sandbox read-only` 防止 Codex 修改文件
- **必须** 使用 `-C` 指定项目根目录
- **建议** 使用 `-o /tmp/codex-output.md` 捕获输出以便回读
- Codex 的输出仅作参考，最终决策由你做出
- 详细用法参见 `/codex` skill（`.claude/commands/codex.md`）
