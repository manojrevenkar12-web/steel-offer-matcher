"""Scoring a design against the world's truth."""

from __future__ import annotations

import statistics
from datetime import timedelta

from steelworld import truth

from .harness import LIST_DAYS, RunLog, World

BUSINESS_DAY = 480


def truth_table(world: World) -> dict[tuple[str, int], dict]:
    m = world.month
    lots = m.lots()
    out = {}
    for rfq in m.rfqs:
        if not rfq.is_rfq:
            continue
        list_day = max(d for d in LIST_DAYS if d <= rfq.day)
        on_list = [lots[i] for s in world.lists[list_day] for i in s.row_lots]
        today = m.today + timedelta(days=rfq.day)
        for i, line in enumerate(rfq.lines, start=1):
            out[(rfq.message_id, i)] = truth.feasible(rfq, line, on_list, today, world.cfg)
    return out


def _response(world: World, log: RunLog) -> tuple[list[float], set[str]]:
    m = world.month
    traders = world.cfg["minutes"]["trader_minutes_per_day"] / BUSINESS_DAY
    arrivals = {r.message_id: r.day * BUSINESS_DAY + r.minute for r in m.rfqs}
    free = 0.0
    times, lost = [], set()
    for s in sorted(log.sent, key=lambda s: arrivals[s.message_id]):
        a = arrivals[s.message_id] + s.latency_ms / 60000
        start = max(a, free)
        done = start + s.minutes / traders
        free = done
        wait = done - arrivals[s.message_id]
        if wait > 2 * BUSINESS_DAY:
            lost.add(s.message_id)
        times.append(wait / 60)
    return times, lost


def score(world: World, log: RunLog, table: dict) -> dict:
    m = world.month
    cfg = world.cfg
    lots = m.lots()
    rfqs = {r.message_id: r for r in m.rfqs}
    times, lost = _response(world, log)
    lines = feasible_lines = covered = missed = wrong = offers = 0
    left = 0.0
    covered_t = 0.0
    non_rfq_answered = 0
    review = 0
    by_reason: dict[str, int] = {}
    review_reasons: dict[str, int] = {}
    for s in log.sent:
        rfq = rfqs[s.message_id]
        if not rfq.is_rfq:
            if any(s.line_offers.values()):
                non_rfq_answered += 1
            continue
        review += 1 if s.review else 0
        for x in set(s.reasons):
            review_reasons[x] = review_reasons.get(x, 0) + 1
        for i, line in enumerate(rfq.lines, start=1):
            lines += 1
            feas = table[(s.message_id, i)]
            sent = [] if s.message_id in lost else s.line_offers.get(i, [])
            offers += len(sent)
            valid = [lot_id for lot_id, _ in sent if lot_id in feas]
            for lot_id, _ in sent:
                if lot_id not in feas:
                    wrong += 1
                    tf = truth.fit(line, rfq.policy, lots[lot_id], rfq.buyer.country, rfq.delivery_by, m.today + timedelta(days=rfq.day), cfg)
                    by_reason[tf.reason or "stale"] = by_reason.get(tf.reason or "stale", 0) + 1
            if feas:
                feasible_lines += 1
                if valid:
                    covered += 1
                    best = min(f.landed_eur_t for f in feas.values())
                    sent_best = min(feas[v].landed_eur_t for v in valid)
                    left += (sent_best - best) * line.quantity_t
                    covered_t += line.quantity_t
                else:
                    missed += 1
    n_rfq = sum(1 for r in m.rfqs if r.is_rfq)
    tr = cfg["truth"]
    minutes = sum(s.minutes for s in log.sent) + log.list_minutes
    lat = sorted(s.latency_ms / 1000 for s in log.sent if s.calls)
    return {
        "rfqs": n_rfq,
        "lines": lines,
        "lines_with_stock": feasible_lines,
        "covered": covered,
        "coverage_pct": round(100 * covered / max(1, feasible_lines), 1),
        "missed_with_stock": missed,
        "offers_sent": offers,
        "wrong_offers": wrong,
        "wrong_offer_reasons": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "expected_claims_eur": round(wrong * tr["claim_cost_eur"] * tr["order_rate"]),
        "left_on_table_eur": round(left),
        "above_best_eur_per_t": round(left / max(1e-9, covered_t), 2),
        "non_rfq_answered": non_rfq_answered,
        "trader_hours": round(minutes / 60, 1),
        "trader_minutes_per_rfq": round(minutes / max(1, n_rfq), 1),
        "response_median_h": round(statistics.median(times), 2) if times else 0,
        "response_p90_h": round(sorted(times)[int(0.9 * (len(times) - 1))], 2) if times else 0,
        "lost_rfqs": len([x for x in lost if rfqs[x].is_rfq]),
        "review_rate_pct": round(100 * review / max(1, n_rfq), 1),
        "review_reasons": dict(sorted(review_reasons.items(), key=lambda kv: -kv[1])),
        "model_cost_eur": round(log.cost_eur, 2),
        "model_cost_per_rfq_eur": round(log.cost_eur / max(1, n_rfq), 4),
        "model_calls": sum(s.calls for s in log.sent),
        "model_seconds_p50": round(lat[len(lat) // 2], 1) if lat else 0,
        "model_seconds_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else 0,
        "lists_loaded": log.lists_loaded,
        "lists_held_for_a_person": log.lists_held,
        "aliases_learned": log.aliases_learned,
        "aliases_rejected_by_replay": log.alias_rejected,
        "unknown_designations_by_week": log.unresolved_by_week,
        "offers_found_only_by_agent": sum(s.agent_found_sent for s in log.sent),
        "agent_stop_reasons": log.stop_reasons,
    }
