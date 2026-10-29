# -*- coding: utf-8 -*-
"""汇总账号当天的跑量/学期进度（只读，供 tick 汇报用）。"""
import os
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lepaorun"))


def term_stats():
    """返回 dict：今日次数、学期总次数/总里程/达标次数/达标里程、最近记录。"""
    try:
        import driver  # 复用 AuthShim（读 config.json 的 token）
        c = driver.AuthShim().make_client()
        terms = c.post("/v3/api.php/WpRun/getTermList", {}) or []
        tid = str(terms[0]["term_id"]) if terms else "4773"
        rr = c.post("/v3/api.php/WpRun/getTermRunRecord", {"term_id": tid, "page": 1}) or {}
        lst = rr.get("list") or []
        day0 = int(datetime.datetime.combine(datetime.date.today(),
                                             datetime.time(0, 0)).timestamp())
        today = [r for r in lst
                 if str(r.get("start_time", "0")).isdigit() and int(r["start_time"]) >= day0]
        return {
            "term_name": terms[0].get("term_name") if terms else "",
            "today_count": len(today),
            "term_count": rr.get("total_num"),
            "term_distance": rr.get("total_distance"),
            "term_ok_count": rr.get("total_score_num"),
            "term_ok_distance": rr.get("total_score_distance"),
        }
    except Exception as e:
        return {"error": str(e)[:80]}


if __name__ == "__main__":
    import json
    print(json.dumps(term_stats(), ensure_ascii=False))
