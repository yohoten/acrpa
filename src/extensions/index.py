# -*- coding: utf-8 -*-
"""extensions.index — 内置白名单清单 (§12.6 信任模型)。

信任模型要点 (路线图 §12.6)
--------------------------
* 远端索引只能回答「有什么」(id / 版本 / URL / 体积 / 最低应用版本 / 变体列表);
* **哈希白名单必须随 ACRPA 二进制分发** —— 即本模块 ``WHITELIST``;
* 安装时以内建 ``sha256`` 为准, 远端提供的哈希一律忽略 (或仅用于交叉告警)。

数据形状::

    WHITELIST = {
        "<ext_id>": {
            "<version>": "<64 位小写 sha256>",
        },
    }

阶段说明
--------
路线图 阶段二新增项③ 起, ``cv.match`` 首个上线扩展的真实 sha256 已在此**逐版本回填**
(由 ``tools/build_cv_match_extension.py --update-index`` 确定性构建后写入)。安装时
``manager.install`` 以内建哈希为准强制校验; 未收录 (ext_id, version) 的包仍按
「未签名」放行 (参见 manager 文档)。
"""

# id → {version: sha256}
WHITELIST = {
    "cv.match": {
        "1.0.0": "d2cd080f930c9e7b123c4c954d95aed13ef9cb1af7999099cf9ecce5c15dea8f",
    },
}


def sha256_for(ext_id, version):
    """→ 该 (ext_id, version) 的内建 sha256; 未收录返回 None (不抛异常)。"""
    try:
        return (WHITELIST.get(ext_id) or {}).get(str(version)) or None
    except Exception:
        return None


def is_whitelisted(ext_id, version):
    """该 (ext_id, version) 是否已在白名单内。"""
    return sha256_for(ext_id, version) is not None


def known_extensions():
    """→ 白名单收录的全部 ext_id 列表 (快照)。"""
    try:
        return sorted(WHITELIST.keys())
    except Exception:
        return []
