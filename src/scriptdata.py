"""ScriptData — the data model for a single automation script row."""
import uuid

import commands


class ScriptData:
    COMMANDS = commands.list_names()
    COLUMNS = ["命令类型"]+["参数{}".format(i) for i in range(1,10)]

    @staticmethod
    def new_id():
        """生成一个非空且（实践上）唯一的行标识 (8 位十六进制 uuid)。"""
        return uuid.uuid4().hex[:8]

    def __init__(self, cmd_type="", args=None, id=None):
        self.cmd_type = cmd_type if cmd_type else ""
        # Handle None values in args - convert to empty string for display
        if args is None:
            self.args = [""]*9
        else:
            self.args = ["" if arg is None else str(arg) for arg in args]
        # 稳定行标识: 不进 to_tuple (保持 10 列 UI 契约), 仅供行身份追踪 /
        # .acrpas 往返 / 撤销快照 (copy.deepcopy 兼容) 使用。
        # 旧调用方不传 id → 自动生成; 空/None 亦回退为自动生成。
        self.id = id or ScriptData.new_id()
        # 步骤级失败策略 (P2, 见 failure_policy): 同样不进 to_tuple ——
        # 不新增常显列, 只在 #0 序号列尾部加角标 + 详情面板编辑。
        # 默认 "" = 沿用全局 stop_on_error, 因此旧脚本/旧调用方行为零变化。
        self.on_error = ""
        self.jump_to = 0

    @classmethod
    def from_xlrd_row(cls, row):
        """Create ScriptData from an xlrd row object."""
        cv = str(row[0].value) if row[0].value is not None else ""
        args = []
        for j in range(1, min(10, len(row))):
            v = row[j].value
            args.append("" if v is None else str(v))
        # Fill remaining args with empty strings
        while len(args) < 9:
            args.append("")
        return cls(cv, args)

    @classmethod
    def from_xlrd_row_values(cls, row_values):
        """Create ScriptData from an xlrd row values list."""
        cv = str(row_values[0]) if row_values[0] is not None else ""
        args = []
        for j in range(1, min(10, len(row_values))):
            v = row_values[j]
            args.append("" if v is None else str(v))
        # Fill remaining args with empty strings
        while len(args) < 9:
            args.append("")
        return cls(cv, args)

    def to_tuple(self): return (self.cmd_type,) + tuple(self.args)
    def get_arg(self, idx): return self.args[idx] if 0<=idx<len(self.args) else ""

    # ── 步骤级失败策略 (P2) ──────────────────────────────────────────
    def set_failure_policy(self, on_error="", jump_to=0):
        """写入失败策略（策略码 + 1 基跳转目标）；非法值由 failure_policy 归一。"""
        import failure_policy
        self.on_error = failure_policy.parse_policy(on_error)
        self.jump_to = failure_policy.normalize_jump_target(jump_to)
        return self.on_error, self.jump_to

    def failure_badge(self):
        """#0 序号列尾部角标（未设置策略 → 空串）。"""
        import failure_policy
        return failure_policy.badge_text(self.on_error, self.jump_to)

    def failure_meta(self):
        """失败策略的可持久化表示（无策略 → 空 dict，保持旧文件不变）。"""
        meta = {}
        if self.on_error:
            meta["on_error"] = self.on_error
        if self.on_error == "jump" and int(self.jump_to or 0) > 0:
            meta["jump_to"] = int(self.jump_to)
        return meta