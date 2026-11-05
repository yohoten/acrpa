# -*- coding: utf-8 -*-
"""纯几何命中检测 (P3-3, maliang 借鉴: 绘制前用几何包围盒做 hit test)。

零 tkinter (P0 纯度护栏): 只做「点 ∈ 矩形 (可外扩 pad)」与「区域索引」查询,
画布层在渲染后把节点包围盒灌入 :class:`RegionIndex`, 命中判定不再逐 mousemove
调用 ``find_overlapping + gettags``。

区域顺序 = 绘制顺序: 查询按「后绘制者优先」反向遍历, 语义对齐现有
``_tags_at`` 的 ``reversed(items)`` (topmost 优先)。
"""


def point_in_rect(x, y, rect, pad=0.0):
    """点 (x, y) 是否落在 rect=(x1, y1, x2, y2) 内 (可外扩 pad)。"""
    try:
        x1, y1, x2, y2 = rect
        return (x1 - pad) <= x <= (x2 + pad) and (y1 - pad) <= y <= (y2 + pad)
    except Exception:
        return False


def detect_point(x, y, regions, pad=0.0):
    """区域列表 ``[(key, rect), ...]`` 中命中点 (x, y) 的首个 key; 无则 None。

    ``regions`` 按绘制顺序正序传入, 查询反向遍历 → 后绘制者优先 (topmost 先命中)。
    """
    for key, rect in reversed(regions):
        if point_in_rect(x, y, rect, pad):
            return key
    return None


class RegionIndex(object):
    """惰性区域索引: ``rebuild(regions)`` 灌入包围盒后 ``query`` 做几何命中。

    与画布解耦, 只持有纯数据 [(key, rect), ...]; 供渲染层在每次全量渲染后刷新。
    """

    def __init__(self, regions=None):
        self._regions = []
        if regions:
            self.rebuild(regions)

    def rebuild(self, regions):
        """灌入 ``[(key, (x1, y1, x2, y2)), ...]`` (按绘制顺序)。"""
        self._regions = list(regions)

    def query(self, x, y, pad=0.0):
        """→ 命中 key 或 None (后绘制者优先)。"""
        return detect_point(x, y, self._regions, pad)

    def boxes(self):
        """→ ``[(key, rect), ...]`` 只读副本 (供调试/断言)。"""
        return list(self._regions)

    def __len__(self):
        return len(self._regions)

    def __bool__(self):
        return bool(self._regions)
