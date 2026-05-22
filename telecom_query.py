"""
电信话费 / 流量 / 通话查询脚本

用法:
    python telecom_query.py                          # 从配置读取，简洁文本输出
    python telecom_query.py --json                   # JSON 完整数据输出
    python telecom_query.py --phone 号码 --password 密码
    python telecom_query.py --packages               # 强制开启所有号码的流量包明细

输出格式（手机号+城市第一行，后续缩进两格）:

    手机号：18012341234  城市：江西南昌
      余额：¥280.62
      本月消费：¥41.90
      通用流量：已用 0 KB / 总 540.00 GB
      专用流量：已用 5.22 GB / 总 640.00 GB
      总流量：已用 5.22 GB / 总 680.49 GB
      查询时间：2026-05-22 19:36:58

配置文件: telecom_config.json（JSON 不支持注释，字段本身即为说明）

    手机号（列表，放最前面）:
        - 号码: 手机号
        - 服务密码: 电信服务密码
        - 输出设置: 可选，该号码的独立输出开关，优先级高于全局

    输出设置（全局默认，1 显示 / 0 隐藏）:
        城市         如「江西南昌」
        余额         如「¥280.62」
        欠费         欠费金额，无欠费则显示「无」
        本月消费     当月已产生费用
        费用明细     套餐 / 增值业务等逐项构成
        通话已用     已用 / 总量（分钟）
        通话剩余     剩余分钟数
        通话分类     省内 / 国内等分类明细
        通用流量     已用 / 总量
        专用流量     专用流量已用 / 总量
        总流量       已用 / 总量
        流量包明细   每行一条，与主输出同级显示
        账单提示     账单周期等提示信息
        查询时间     数据获取的时间戳
"""

import json
import os
import sys
import re
import base64
import random
import argparse
import smtplib
import hashlib
import hmac as _hmac
import urllib.parse
import certifi
import requests
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime
from pathlib import Path
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

# ============================================================
#  路径
# ============================================================
SCRIPT_DIR   = Path(__file__).parent
DATA_DIR     = Path(os.environ.get("TELECOM_DATA_DIR", SCRIPT_DIR))
TOKEN_DIR    = DATA_DIR / "telecom_tokens"
TOKEN_FILE   = TOKEN_DIR / "tokens.json"
FAIL_FILE    = TOKEN_DIR / "fail_count.json"
CONFIG_FILE  = DATA_DIR / "telecom_config.json"
CONFIG_OLD   = SCRIPT_DIR / "telecom_accounts.json"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
#  中文字段名 → 内部字段名 映射（配置文件用中文，代码用英文）
# ============================================================
FIELD_MAP = {
    "城市":       "city",
    "余额":       "balance",
    "欠费":       "arrear",
    "本月消费":   "month_bill",
    "费用明细":   "bill_detail",
    "通话已用":   "voice_used",
    "通话剩余":   "voice_remaining",
    "通话分类":   "voice_categories",
    "通用流量":   "general_flow",
    "专用流量":   "special_flow",
    "总流量":     "total_flow",
    "流量包明细": "flow_packages",
    "账单提示":   "cycle_tips",
    "查询时间":   "query_time",
}

# ============================================================
#  默认输出配置（1 = 显示，0 = 隐藏）
# ============================================================
DEFAULT_OUTPUT = {
    "city":             1,   # 所在省市
    "balance":          1,   # 账户余额
    "arrear":           0,   # 欠费金额
    "month_bill":       1,   # 本月已产生费用
    "bill_detail":      0,   # 费用构成明细
    "voice_used":       1,   # 通话已用 / 总量
    "voice_remaining":  0,   # 通话剩余分钟
    "voice_categories": 0,   # 通话分类明细
    "general_flow":     1,   # 通用流量
    "special_flow":     1,   # 专用流量
    "total_flow":       1,   # 总流量
    "flow_packages":    0,   # 所有流量包逐条
    "cycle_tips":       0,   # 账单周期提示
    "query_time":       1,   # 查询时间戳
}

# ============================================================
#  加密
# ============================================================
RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDBkLT15ThVgz6/NOl6s8GNPofd
WzWbCkWnkaAm7O2LjkM1H7dMvzkiqdxU02jamGRHLX/ZNMCXHnPcW/sDhiFCBN18
qFvy8g6VYb9QtroI09e176s+ZCtiv7hbin2cCTj99iUpnEloZm19lwHyo69u5UMi
PMpq0/XKBO8lYhN/gwIDAQAB
-----END PUBLIC KEY-----"""

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json; charset=UTF-8",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
    "user-agent": "P216010901",
}
CLIENT_TYPE = "#12.2.0#channel50#iPhone 14 Pro#"


def caesar_shift(text, encode=True):
    """凯撒移位：加密 +2，解密 -2"""
    offset = 2 if encode else -2
    return "".join(chr(ord(c) + offset & 65535) for c in text)


def rsa_encrypt(text):
    """RSA 公钥加密 + Base64 编码"""
    pub_key = RSA.import_key(RSA_PUBLIC_KEY.encode())
    cipher = PKCS1_v1_5.new(pub_key)
    return base64.b64encode(cipher.encrypt(text.encode("utf-8"))).decode("utf-8")


# ============================================================
#  Token 持久化
# ============================================================
def load_tokens():
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return {}


def save_tokens(data):
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
#  登录失败风控保护
# ============================================================
FAIL_LIMIT = 5  # 连续失败上限

def load_fail_count():
    if FAIL_FILE.exists():
        return json.loads(FAIL_FILE.read_text(encoding="utf-8"))
    return {}


def save_fail_count(data):
    FAIL_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
#  核心接口
# ============================================================
def do_login(session, phonenum, password):
    """登录电信接口，返回 token + 省市编码与名称"""
    uuid = str(random.randint(10**15, 10**16 - 1))
    ts   = datetime.now().strftime("%Y%m%d%H%M%S")
    enc  = f"iPhone 14 13.2.{uuid[:12]}{phonenum}{ts}{password}0$$$0."

    body = {
        "content": {
            "fieldData": {
                "accountType":                  "",
                "authentication":               caesar_shift(password),
                "deviceUid":                    uuid[:16],
                "isChinatelecom":               "",
                "loginAuthCipherAsymmertric":   rsa_encrypt(enc),
                "loginType":                    "4",
                "phoneNum":                     caesar_shift(phonenum),
                "systemVersion":                "13.2.3",
            },
            "attach": "test",
        },
        "headerInfos": {
            "code":           "userLoginNormal",
            "clientType":     CLIENT_TYPE,
            "timestamp":      ts,
            "shopId":         "20002",
            "source":         "110003",
            "sourcePassword": "Sid98s",
            "token":          "",
            "userLoginName":  caesar_shift(phonenum),
        },
    }

    resp = session.post(
        "https://appgologin.189.cn:9031/login/client/userLoginNormal",
        headers=HEADERS, json=body, timeout=15,
    )
    result = resp.json().get("responseData", {}).get("data", {}).get("loginSuccessResult")
    if not result:
        raise Exception(f"登录失败: {resp.json()}")

    return {
        "token":        result.get("token", ""),
        "provinceCode": result.get("provinceCode", ""),
        "cityCode":     result.get("cityCode", ""),
        "provinceName": result.get("provinceName", ""),
        "cityName":     result.get("cityName", ""),
        "phonenum":     phonenum,
        "password":     password,
    }


def _query(session, code, phonenum, token, field_data):
    """通用查询封装：构造 headerInfos + fieldData 并发 POST"""
    ts = datetime.now().strftime("%Y%m%d%H%M00")
    body = {
        "content": {
            "fieldData": {**field_data, "account": caesar_shift(phonenum)},
            "attach": "test",
        },
        "headerInfos": {
            "code":           code,
            "clientType":     CLIENT_TYPE,
            "timestamp":      ts,
            "shopId":         "20002",
            "source":         "110003",
            "sourcePassword": "Sid98s",
            "userLoginName":  caesar_shift(phonenum),
            "token":          token,
        },
    }
    resp = session.post(
        f"https://appfuwu.189.cn:9021/query/{code}",
        headers=HEADERS, json=body, timeout=15,
    )
    return resp.json()


def qry_important_data(session, phonenum, token, province_code="", city_code=""):
    """查询余额 / 流量 / 通话等主要数据"""
    return _query(session, "qryImportantData", phonenum, token, {
        "provinceCode": province_code or "600101",
        "cityCode":     city_code or "8441900",
        "shopId":       "20002",
        "isChinatelecom": "0",
    })


def user_flux_package(session, phonenum, token, **_):
    """查询流量包明细"""
    return _query(session, "userFluxPackage", phonenum, token, {
        "queryFlag":  "0",
        "accessAuth": "1",
    })


def qry_share_usage(session, phonenum, token, billing_cycle=None):
    """查询共享套餐用量（主副卡），返回解密后的号码列表和各类型用量"""
    if billing_cycle is None:
        billing_cycle = datetime.now().strftime("%Y%m")
    data = _query(session, "qryShareUsage", phonenum, token, {
        "billingCycle": billing_cycle,
    })
    # 解密号码字段
    for item in data.get("responseData", {}).get("data", {}).get("sharePhoneBeans", []):
        item["sharePhoneNum_decrypted"] = caesar_shift(item.get("sharePhoneNum", ""), encode=False)
    for st in data.get("responseData", {}).get("data", {}).get("shareTypeBeans", []):
        for sa in _iter_share_amounts(st):
            sa["phoneNum_decrypted"] = caesar_shift(sa.get("phoneNum", ""), encode=False)
    return data


def _iter_share_amounts(share_type):
    """遍历共享用量结构中的 phoneNum 列表"""
    for si in share_type.get("shareUsageInfos", []):
        yield from si.get("shareUsageAmounts", [])


# ============================================================
#  数据解析
# ============================================================
def kb_to_human(kb_str):
    """KB 数值 → 可读字符串"""
    try:
        kb = float(kb_str)
    except (TypeError, ValueError):
        return "0 KB"
    if kb == 0:
        return "0 KB"
    if kb < 1024:
        return f"{kb:.0f} KB"
    if kb < 1024 * 1024:
        return f"{kb / 1024:.2f} MB"
    return f"{kb / 1024 / 1024:.2f} GB"


def _parse_flow(obj, pkg_total_kb=0):
    """解析单个流量对象（字段单位 KB），pkg_total_kb 从流量包反算的总量的后备值"""
    if not obj:
        obj = {}
    used_kb  = int(float(obj.get("used", 0) or 0))
    total_kb = int(float(obj.get("total", 0) or 0))
    bal_kb   = int(float(obj.get("balance", 0) or 0))
    over_kb  = int(float(obj.get("over", 0) or 0))
    # API 没返回 total 时，用流量包求和值补上
    if total_kb == 0 and pkg_total_kb > 0:
        total_kb = pkg_total_kb
    return {
        "used_kb":     used_kb,
        "total_kb":    total_kb,
        "balance_kb":  bal_kb,
        "over_kb":     over_kb,
        "used_human":      kb_to_human(str(used_kb)),
        "total_human":     kb_to_human(str(total_kb)),
        "balance_human":   kb_to_human(str(bal_kb)),
    }


def _human_to_kb(text):
    """将 '17.84GB' / '500.00MB' / '/共17.84GB' 等文本转为 KB"""
    import re as _re
    if not text:
        return 0
    text = _re.sub(r"^(.*/)", "", text.strip())  # 去掉 "/共" 前缀
    text = _re.sub(r"[^\d.]", "", text)           # 只留数字和小数点
    try:
        val = float(text)
    except ValueError:
        return 0
    upper = text.upper()
    if "G" in upper or "G" in "".join(c for c in text if c.isalpha()):
        pass  # val 已经是 GB
    if val > 0:
        return int(val * 1024 * 1024)  # 按 GB 算
    return 0


def _sum_pkgs_by_category(packages, keyword):
    """从流量包明细中按 category 关键词求和 total（KB）"""
    total = 0
    for p in packages:
        if keyword in p.get("category", ""):
            total += _human_to_kb(p.get("total", ""))
    return total


def to_summary(raw_data, phonenum="", province_name="", city_name="", pkg_list=None):
    """原始 API 数据 → 结构化摘要

    pkg_list: parse_flux_packages 返回的 packages 列表，
              用于在 API 未返回 total 时从流量包反算。
    """
    data = raw_data.get("responseData", {}).get("data", {})
    bi   = data.get("balanceInfo", {})
    fi   = data.get("flowInfo", {})
    pkgs = pkg_list or []

    # 从流量包按 category 反算通用/专用 total
    common_pkg_total  = _sum_pkgs_by_category(pkgs, "通用")
    special_pkg_total = _sum_pkgs_by_category(pkgs, "专用")

    # 余额
    idx = bi.get("indexBalanceDataInfo", {})
    bill_text = bi.get("phoneBillRegion", {}).get("subTitleHh", "0元")
    month_bill = 0.0
    try:
        month_bill = float(re.sub(r"[^\d.]", "", bill_text))
    except (TypeError, ValueError):
        pass

    # 语音
    vd = data.get("voiceInfo", {}).get("voiceDataInfo", {})
    voice_cats = [{
        "title": b.get("title", ""), "used": b.get("leftTitleHh", ""),
        "remaining": b.get("rightTitleHh", ""), "total": b.get("rightTitleEnd", ""),
    } for b in data.get("voiceInfo", {}).get("voiceBars", [])]

    # 流量列表
    flow_list = [{
        "title": f.get("title", ""), "used": f.get("leftTitleHh", ""),
        "remaining": f.get("rightTitleHh", ""), "total": f.get("rightTitleEnd", ""),
    } for f in fi.get("flowList", [])]

    return {
        "phonenum":         phonenum,
        "region_name":      f"{province_name}{city_name}" if province_name or city_name else "未知",
        "balance_yuan":     float(idx.get("balance", 0)),
        "arrear_yuan":      float(idx.get("arrear", 0)),
        "month_bill_yuan":  month_bill,
        "bill_detail":      [{"name": b.get("title", ""), "amount": b.get("barRightSubTitle", "")}
                             for b in bi.get("phoneBillBars", [])],
        "cycle_tips":       bi.get("loopTips", []),
        "voice_total":      int(vd.get("total") or 0),
        "voice_balance":    int(vd.get("balance") or 0),
        "voice_used":       int(vd.get("used") or 0),
        "voice_categories": voice_cats,
        "common_flow":      _parse_flow(fi.get("commonFlow", {}), common_pkg_total),
        "special_flow":     _parse_flow(fi.get("specialAmount", {}), special_pkg_total),
        "total_flow":       _parse_flow(fi.get("totalAmount", {})),
        "flow_used_display": fi.get("flowRegion", {}).get("subTitleHh", ""),
        "flow_list":        flow_list,
    }


def parse_flux_packages(raw_data):
    """流量包明细 → 结构化列表"""
    data = raw_data.get("responseData", {}).get("data", {})
    main = data.get("mainProductOFFInfo", {})

    packages = []
    for cat in data.get("productOFFRatable", {}).get("ratableResourcePackages", []):
        for pkg in cat.get("productInfos", []):
            exp = pkg.get("outOfServiceTime", "")
            packages.append({
                "category":  cat.get("title", ""),
                "name":      pkg.get("title", "未知流量包"),
                "used":      pkg.get("leftHighlight", "0KB"),
                "remaining": pkg.get("rightHighlight", "0KB"),
                "total":     pkg.get("rightCommon", ""),
                "expire":    exp.replace("失效时间：", "") if exp else "",
                "is_invalid": pkg.get("isInvalid", "0") == "1",
            })

    return {
        "plan_name": main.get("productOFFName", ""),
        "share_tip": main.get("shareTipDesc", ""),
        "packages":  packages,
    }


# ============================================================
#  主查询流程
# ============================================================
def query_single(phone, password, include_packages=False):
    """查询单个号码的完整数据（含 token 缓存和自动重登）"""
    session = requests.Session()
    session.verify = certifi.where()

    tokens    = load_tokens()
    cached    = tokens.get(phone, {}).get("login_info", {})
    token     = cached.get("token", "")
    prov_code = cached.get("provinceCode", "")
    city_code = cached.get("cityCode", "")
    cached.setdefault("provinceName", "")
    cached.setdefault("cityName", "")

    need_login = not token or not _token_alive(session, phone, token, prov_code, city_code)
    if need_login:
        cached = _do_login_and_cache(session, tokens, phone, password)
        token, prov_code, city_code = cached["token"], cached["provinceCode"], cached["cityCode"]

    raw = qry_important_data(session, phone, token, prov_code, city_code)
    if raw.get("headerInfos", {}).get("code") == "X201":
        cached = _do_login_and_cache(session, tokens, phone, password)
        token, prov_code, city_code = cached["token"], cached["provinceCode"], cached["cityCode"]
        raw = qry_important_data(session, phone, token, prov_code, city_code)

    summary = to_summary(raw, phone, cached.get("provinceName", ""), cached.get("cityName", ""))
    summary["queried_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 始终请求流量包明细：用于反算通用/专用 total（API 经常不返回）
    pkg_list = []
    try:
        pkg_raw = user_flux_package(session, phone, token, province_code=prov_code, city_code=city_code)
        parsed  = parse_flux_packages(pkg_raw)
        pkg_list = parsed.get("packages", [])
        if include_packages:
            summary["packages"] = parsed
    except Exception as e:
        if include_packages:
            summary["packages"], summary["packages_error"] = [], str(e)

    # 如果 API 的 common_flow / special_flow total 为 0，用流量包反算补上
    if not summary["common_flow"]["total_kb"] or not summary["special_flow"]["total_kb"]:
        common_sum  = _sum_pkgs_by_category(pkg_list, "通用")
        special_sum = _sum_pkgs_by_category(pkg_list, "专用")
        if not summary["common_flow"]["total_kb"] and common_sum:
            summary["common_flow"]["total_kb"] = common_sum
            summary["common_flow"]["total_human"] = kb_to_human(str(common_sum))
        if not summary["special_flow"]["total_kb"] and special_sum:
            summary["special_flow"]["total_kb"] = special_sum
            summary["special_flow"]["total_human"] = kb_to_human(str(special_sum))

    try:
        sd = qry_share_usage(session, phone, token).get("responseData", {}).get("data", {})
        summary["share_usage"] = {
            "phones": [{"order": i.get("order", ""),
                        "phone_decrypted": i.get("sharePhoneNum_decrypted", "")}
                       for i in sd.get("sharePhoneBeans", [])],
            "types":  [{"type": st.get("shareType", ""),
                        "usages": [{"phone_decrypted": sa.get("phoneNum_decrypted", ""),
                                    "usage": sa.get("usageAmount", "0")}
                                   for sa in _iter_share_amounts(st)]}
                       for st in sd.get("shareTypeBeans", [])],
        }
    except Exception as e:
        summary["share_usage"], summary["share_usage_error"] = None, str(e)

    return summary


def _token_alive(session, phone, token, prov_code, city_code):
    """检测缓存的 token 是否仍然有效"""
    try:
        raw = qry_important_data(session, phone, token, prov_code, city_code)
        code = raw.get("headerInfos", {}).get("code", "")
        return code not in ("X201", "0200") and bool(raw.get("responseData", {}).get("data"))
    except Exception:
        return False


def _do_login_and_cache(session, tokens, phone, password):
    """登录并写入缓存，返回 login_info 字典（含风控保护）"""
    fail_counts = load_fail_count()
    fail_num = fail_counts.get(phone, 0)
    if fail_num >= FAIL_LIMIT:
        raise RuntimeError(
            f"号码 {phone} 已连续登录失败 {fail_num} 次，为避免风控已停止。"
            f"如需重试请删除 {FAIL_FILE} 中的对应条目。"
        )
    try:
        info = do_login(session, phone, password)
    except Exception as e:
        fail_counts[phone] = fail_num + 1
        save_fail_count(fail_counts)
        raise RuntimeError(
            f"登录失败（已连续失败 {fail_num + 1}/{FAIL_LIMIT} 次）：{e}"
        ) from e
    # 登录成功，清零失败计数
    if phone in fail_counts:
        del fail_counts[phone]
        save_fail_count(fail_counts)
    tokens[phone] = {
        "login_info": {
            "token": info["token"], "provinceCode": info["provinceCode"],
            "cityCode": info["cityCode"], "provinceName": info["provinceName"],
            "cityName": info["cityName"],
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_tokens(tokens)
    return info


# ============================================================
#  输出格式化
# ============================================================
def format_human_readable(results, output_configs=None):
    """查询结果 → 简洁可读文本"""
    lines = []
    for i, r in enumerate(results):
        phone = r.get("phonenum", r.get("phone", ""))
        cfg   = _merged_config(phone, output_configs)

        if "error" in r:
            lines.append(f"手机号：{phone}  [失败] {r['error']}")
            lines.append("")
            continue

        lines.append(f"手机号：{phone}" + (f"  城市：{r['region_name']}" if cfg["city"] else ""))

        if cfg["balance"]:
            lines.append(f"  余额：¥{r['balance_yuan']:.2f}")

        if cfg["arrear"]:
            v = r["arrear_yuan"]
            lines.append(f"  欠费：¥{v:.2f}" if v > 0 else "  欠费：无")

        if cfg["month_bill"]:
            lines.append(f"  本月消费：¥{r['month_bill_yuan']:.2f}")

        if cfg["bill_detail"]:
            for b in r.get("bill_detail", []):
                lines.append(f"    {b['name']}：{b['amount']}")

        if cfg["voice_used"]:
            t = r["voice_total"]
            lines.append(f"  通话已用：{r['voice_used']}/{t} 分钟" if t else "  通话已用：未开通语音套餐")

        if cfg["voice_remaining"]:
            lines.append(f"  通话剩余：{r['voice_balance']} 分钟")

        if cfg["voice_categories"]:
            for vc in r.get("voice_categories", []):
                lines.append(f"    {vc['title']}：{vc['used']}/{vc['total']}")

        if cfg["general_flow"]:
            f = r["common_flow"]
            lines.append(f"  通用流量：已用 {f['used_human']} / 总 {f['total_human']}")

        if cfg["special_flow"]:
            f = r["special_flow"]
            lines.append(f"  专用流量：已用 {f['used_human']} / 总 {f['total_human']}")

        if cfg["total_flow"]:
            f = r["total_flow"]
            lines.append(f"  总流量：已用 {f['used_human']} / 总 {f['total_human']}")

        if cfg["flow_packages"] and r.get("packages"):
            pkgs = r["packages"]
            if pkgs.get("plan_name"):
                lines.append(f"  套餐：{pkgs['plan_name']}")
            for p in pkgs.get("packages", []):
                tag = " [已失效]" if p.get("is_invalid") else ""
                lines.append(f"  {p['name']}：{p['used']}/{p['total']}{tag}")

        if cfg["cycle_tips"]:
            for tip in r.get("cycle_tips", []):
                lines.append(f"  提示：{tip}")

        if cfg["query_time"]:
            lines.append(f"  查询时间：{r['queried_at']}")

        if i < len(results) - 1:
            lines.append("")

    return "\n".join(lines)


def _merged_config(phone, output_configs):
    """合并 DEFAULT_OUTPUT + 全局配置 + 单号配置"""
    cfg = dict(DEFAULT_OUTPUT)
    if output_configs and phone in output_configs:
        cfg.update(output_configs[phone])
    return cfg


# ============================================================
#  配置加载（中文字段 → 内部字段自动转换）
# ============================================================
def _translate_output(raw):
    """将中文 key 的输出配置翻译为内部英文 key"""
    result = {}
    for cn_key, val in raw.items():
        en_key = FIELD_MAP.get(cn_key, cn_key)
        result[en_key] = val
    return result


def load_config():
    """加载配置文件，返回 (accounts, global_output, notify_config)，accounts 中 phone/password 已用内部 key"""
    if not CONFIG_FILE.exists():
        if CONFIG_OLD.exists():
            return json.loads(CONFIG_OLD.read_text(encoding="utf-8")), {}, {}
        return [], {}, {}

    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    # 账号列表：中文 key → 内部 key
    accounts = []
    for entry in data.get("手机号", []):
        acct = {"phone": entry.get("号码", ""), "password": entry.get("服务密码", "")}
        if "输出设置" in entry:
            acct["output"] = _translate_output(entry["输出设置"])
        accounts.append(acct)

    # 全局输出设置
    global_output = _translate_output(data.get("输出设置", {}))

    # 通知推送配置
    notify_config = data.get("通知推送", {})

    return accounts, global_output, notify_config


# ============================================================
#  通知推送模块
# ============================================================

def _notify_smtp(title, body, cfg):
    """SMTP 邮件推送"""
    server   = cfg.get("SMTP服务器", "")
    port     = int(cfg.get("SMTP端口", 465))
    use_ssl  = str(cfg.get("SMTP_SSL", "true")).lower() == "true"
    sender   = cfg.get("发件邮箱", "")
    pwd      = cfg.get("邮箱密码或授权码", "")
    name     = cfg.get("发件人昵称", "电信查询")
    receiver = cfg.get("收件邮箱", "") or sender
    if not all([server, sender, pwd]):
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = formataddr((Header(name, "utf-8").encode(), sender))
    msg["To"]      = formataddr((Header(name, "utf-8").encode(), receiver))
    msg["Subject"] = Header(title, "utf-8")
    try:
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        srv = smtp_cls(server, port)
        srv.login(sender, pwd)
        srv.sendmail(sender, [r.strip() for r in receiver.split(",")], msg.as_bytes())
        srv.quit()
        print("[通知] SMTP 邮件推送成功")
    except Exception as e:
        print(f"[通知] SMTP 推送失败: {e}")


def _notify_pushplus(title, body, cfg):
    """PushPlus 微信推送"""
    token = cfg.get("PushPlus令牌", "")
    if not token:
        return
    try:
        resp = requests.post("https://www.pushplus.plus/send", json={
            "token": token, "title": title, "content": body,
            "template": "txt", "channel": "wechat",
        }, timeout=10)
        r = resp.json()
        if r.get("code") == 200:
            print("[通知] PushPlus 推送成功")
        else:
            print(f"[通知] PushPlus 推送失败: {r.get('msg', '')}")
    except Exception as e:
        print(f"[通知] PushPlus 推送失败: {e}")


def _notify_serverchan(title, body, cfg):
    """Server酱 微信推送"""
    key = cfg.get("Server酱密钥", "")
    if not key:
        return
    try:
        match = re.match(r"sctp(\d+)t", key)
        url = f"https://{match.group(1)}.push.ft07.com/send/{key}.send" if match else f"https://sctapi.ftqq.com/{key}.send"
        resp = requests.post(url, data={"text": title, "desp": body.replace("\n", "\n\n")}, timeout=10)
        r = resp.json()
        if r.get("errno") == 0 or r.get("code") == 0:
            print("[通知] Server酱 推送成功")
        else:
            print(f"[通知] Server酱 推送失败: {r.get('message', '')}")
    except Exception as e:
        print(f"[通知] Server酱 推送失败: {e}")


def _notify_bark(title, body, cfg):
    """Bark iOS 推送"""
    key = cfg.get("Bark设备码", "")
    if not key:
        return
    url = key if key.startswith("http") else f"https://api.day.app/{key}"
    try:
        resp = requests.post(url, json={"title": title, "body": body}, timeout=10)
        if resp.json().get("code") == 200:
            print("[通知] Bark 推送成功")
        else:
            print("[通知] Bark 推送失败")
    except Exception as e:
        print(f"[通知] Bark 推送失败: {e}")


def _notify_telegram(title, body, cfg):
    """Telegram Bot 推送"""
    bot_token = cfg.get("Telegram机器人Token", "")
    user_id   = cfg.get("Telegram用户ID", "")
    if not bot_token or not user_id:
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": user_id, "text": f"{title}\n\n{body}", "disable_web_page_preview": "true"},
            timeout=10,
        )
        if resp.json().get("ok"):
            print("[通知] Telegram 推送成功")
        else:
            print("[通知] Telegram 推送失败")
    except Exception as e:
        print(f"[通知] Telegram 推送失败: {e}")


def _notify_wecom_bot(title, body, cfg):
    """企业微信机器人推送"""
    key = cfg.get("企业微信机器人Key", "")
    if not key:
        return
    try:
        resp = requests.post(
            f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}",
            json={"msgtype": "text", "text": {"content": f"{title}\n\n{body}"}},
            timeout=10,
        )
        if resp.json().get("errcode") == 0:
            print("[通知] 企业微信 推送成功")
        else:
            print("[通知] 企业微信 推送失败")
    except Exception as e:
        print(f"[通知] 企业微信 推送失败: {e}")


def _notify_dingtalk(title, body, cfg):
    """钉钉机器人推送"""
    token  = cfg.get("钉钉机器人Token", "")
    secret = cfg.get("钉钉机器人密钥", "")
    if not token or not secret:
        return
    try:
        ts = str(round(datetime.now().timestamp() * 1000))
        string_to_sign = f"{ts}\n{secret}"
        sign = urllib.parse.quote_plus(
            base64.b64encode(_hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest())
        )
        url = f"https://oapi.dingtalk.com/robot/send?access_token={token}&timestamp={ts}&sign={sign}"
        resp = requests.post(url, json={
            "msgtype": "text", "text": {"content": f"{title}\n\n{body}"},
        }, timeout=10)
        if not resp.json().get("errcode"):
            print("[通知] 钉钉 推送成功")
        else:
            print("[通知] 钉钉 推送失败")
    except Exception as e:
        print(f"[通知] 钉钉 推送失败: {e}")


def _notify_feishu(title, body, cfg):
    """飞书机器人推送"""
    key = cfg.get("飞书机器人Key", "")
    if not key:
        return
    try:
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/bot/v2/hook/{key}",
            json={"msg_type": "text", "content": {"text": f"{title}\n\n{body}"}},
            timeout=10,
        )
        if resp.json().get("code") == 0 or resp.json().get("StatusCode") == 0:
            print("[通知] 飞书 推送成功")
        else:
            print("[通知] 飞书 推送失败")
    except Exception as e:
        print(f"[通知] 飞书 推送失败: {e}")


def _notify_webhook(title, body, cfg):
    """自定义 Webhook 推送"""
    url    = cfg.get("自定义Webhook地址", "")
    method = cfg.get("自定义Webhook方法", "POST").upper()
    tpl    = cfg.get("自定义Webhook请求体", "")
    if not url:
        return
    try:
        req_body = tpl.replace("$title", title).replace("$content", body) if tpl else body
        headers  = {"Content-Type": "application/json"} if tpl else {}
        resp = requests.request(method, url, data=req_body, headers=headers, timeout=10)
        if resp.status_code == 200:
            print("[通知] 自定义 Webhook 推送成功")
        else:
            print(f"[通知] 自定义 Webhook 推送失败: HTTP {resp.status_code}")
    except Exception as e:
        print(f"[通知] 自定义 Webhook 推送失败: {e}")


def send_notify(title, body, notify_config):
    """统一推送入口：根据配置自动启用已填写的渠道"""
    push_cfg   = notify_config.get("推送渠道", {})
    extra_cfg  = notify_config.get("可选渠道", {})
    # 合并两级配置（可选渠道字段名不重复则无冲突）
    merged = {**extra_cfg, **push_cfg}
    # 按优先级依次推送
    _notify_smtp(title, body, merged)
    _notify_pushplus(title, body, merged)
    _notify_serverchan(title, body, merged)
    _notify_bark(title, body, merged)
    _notify_telegram(title, body, merged)
    _notify_wecom_bot(title, body, merged)
    _notify_dingtalk(title, body, merged)
    _notify_feishu(title, body, merged)
    _notify_webhook(title, body, merged)


# ============================================================
#  入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="电信话费 / 流量 / 通话查询")
    parser.add_argument("--phone",     help="手机号（支持多个）", nargs="*")
    parser.add_argument("--password",  help="服务密码（仅单号时有效）", default="")
    parser.add_argument("--json",      help="JSON 格式输出完整数据", action="store_true")
    parser.add_argument("--packages",  help="强制开启流量包明细", action="store_true")
    args = parser.parse_args()

    accounts, global_output, notify_config = load_config()

    # CLI 参数覆盖
    if args.phone:
        pw_map = {a["phone"]: a["password"] for a in accounts}
        if len(args.phone) == 1 and args.password:
            accounts = [{"phone": args.phone[0], "password": args.password}]
        else:
            accounts = [{"phone": p, "password": pw_map.get(p, "")} for p in args.phone]

    if not accounts:
        print("错误：未配置账号，请通过 --phone 或 telecom_config.json 配置。")
        sys.exit(1)

    # 合并输出配置：全局默认 < 配置文件全局 < 单号覆盖 < CLI --packages
    output_configs = {}
    for acct in accounts:
        cfg = dict(DEFAULT_OUTPUT)
        cfg.update(global_output)
        cfg.update(acct.get("output", {}))
        output_configs[acct["phone"]] = cfg

    if args.packages:
        for cfg in output_configs.values():
            cfg["flow_packages"] = 1

    # 逐号查询
    results = []
    for acct in accounts:
        phone, pw = acct["phone"], acct["password"]
        if not pw:
            results.append({"phone": phone, "error": "未配置服务密码"})
            continue
        try:
            need_pkg = output_configs.get(phone, {}).get("flow_packages", 0) == 1
            results.append(query_single(phone, pw, include_packages=need_pkg))
        except Exception as e:
            results.append({"phone": phone, "error": str(e)})

    print(json.dumps(results, ensure_ascii=False, indent=2) if args.json
          else format_human_readable(results, output_configs))

    # 通知推送
    if notify_config.get("启用") and not args.json:
        body = format_human_readable(results, output_configs)
        print("\n[通知] 正在推送...")
        send_notify("【电信套餐用量监控】", body, notify_config)


if __name__ == "__main__":
    main()
