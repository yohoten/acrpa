"""ScriptData — the data model for a single automation script row."""
import commands


class ScriptData:
    COMMANDS = commands.list_names()
    COLUMNS = ["命令类型"]+["参数{}".format(i) for i in range(1,10)]

    def __init__(self, cmd_type="", args=None):
        self.cmd_type = cmd_type if cmd_type else ""
        # Handle None values in args - convert to empty string for display
        if args is None:
            self.args = [""]*9
        else:
            self.args = ["" if arg is None else str(arg) for arg in args]

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