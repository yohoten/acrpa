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