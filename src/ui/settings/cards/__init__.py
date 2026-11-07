# -*- coding: utf-8 -*-
"""ui.settings.cards — 设置窗口 11 张卡 (每卡导出 build(parent, ctx) / apply(ctx, handles))。

卡片顺序 (由 ui.settings.window._CARD_KEYS 决定, 顺序即导航与 grid 行序):
    exec(0) ai(1) record(2) log(3) system(4) quick(5) advanced(6)
    netlink(7) python(8) market(9) extensions(10)

约束: 卡片 **不得 import ACRPA**; 全部宿主依赖经 build(parent, ctx) 的 ctx 注入。
"""
