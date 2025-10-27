# -*- coding: utf-8 -*-
from __future__ import annotations
import time, hashlib
from typing import Dict, Any


def make(ctx: Dict[str, Any]):
    home = ctx['home']
    scan_mailboxes = ctx['scan_mailboxes']
    mbox_idx = ctx['mbox_idx']
    print_block = ctx['print_block']
    log_ledger = ctx['log_ledger']
    outbox_write = ctx['outbox_write']
    compose_sentinel = ctx['compose_sentinel']
    sha256_text = ctx['sha256_text']
    events_api = ctx['events_api']
    ack_receiver = ctx['ack_receiver']
    should_forward = ctx['should_forward']
    send_handoff = ctx['send_handoff']
    policies = ctx['policies']
    state = ctx['state']
    mbox_counts = ctx['mbox_counts']
    mbox_last = ctx['mbox_last']
    last_event_ts = ctx['last_event_ts']
    write_status = ctx['write_status']
    write_queue_and_locks = ctx['write_queue_and_locks']
    deliver_paused_box = ctx['deliver_paused_box']

    def process():
        events = scan_mailboxes(home, mbox_idx)
        payload = ""
        if events["peerA"].get("to_user"):
            txt = events["peerA"]["to_user"].strip()
            print_block("PeerA → USER", txt)
            try:
                eid = hashlib.sha1(txt.encode('utf-8', errors='ignore')).hexdigest()[:12]
            except Exception:
                eid = str(int(time.time()))
            try:
                log_ledger(home, {"from":"PeerA","kind":"to_user","eid": eid, "chars": len(txt)})
            except Exception:
                pass
            outbox_write(home, {"type":"to_user","peer":"PeerA","text":txt,"eid":eid})
            ack_receiver("PeerA", events["peerA"]["to_user"])
            mbox_counts["peerA"]["to_user"] += 1
            mbox_last["peerA"]["to_user"] = time.strftime("%H:%M:%S")
            last_event_ts["PeerA"] = time.time()
            try:
                tsz = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                sha8 = sha256_text(txt)[:8]
                sentinel = compose_sentinel(ts=tsz, eid=eid, sha8=sha8, route="PeerA→User")
                (home/"mailbox"/"peerA"/"to_user.md").write_text(sentinel, encoding="utf-8")
            except Exception:
                pass
        if events["peerA"].get("to_peer"):
            payload = events["peerA"]["to_peer"].strip()
            try:
                log_ledger(home, {"from":"PeerA","kind":"to_peer-seen","route":"mailbox","chars":len(payload)})
            except Exception:
                pass
            events_api.ledger_events_from_payload("PeerA", payload)
            ack_receiver("PeerA", payload)
            mbox_counts["peerA"]["to_peer"] += 1
            mbox_last["peerA"]["to_peer"] = time.strftime("%H:%M:%S")
            last_event_ts["PeerA"] = time.time()
            try:
                if events_api.teach_intercept_missing_insight("PeerA", payload):
                    payload = ""
            except Exception:
                pass
        if payload:
            if should_forward(payload, "PeerA", "PeerB", policies, state, override_enabled=False):
                wrapped = f"<FROM_PeerA>\n{payload}\n</FROM_PeerA>\n"
                send_handoff("PeerA", "PeerB", wrapped)
                try:
                    log_ledger(home, {"from":"PeerA","to":"PeerB","kind":"to_peer-forward","route":"mailbox","chars":len(payload)})
                except Exception:
                    pass
                try:
                    eid2 = hashlib.sha1(payload.encode('utf-8','ignore')).hexdigest()[:12]
                    outbox_write(home, {"type":"to_peer_summary","from":"PeerA","to":"PeerB","text": payload, "eid": eid2})
                except Exception:
                    eid2 = str(int(time.time()))
                try:
                    tsz = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                    sha8 = sha256_text(payload)[:8]
                    sentinel = compose_sentinel(ts=tsz, eid=eid2, sha8=sha8, route="PeerA→PeerB")
                    (home/"mailbox"/"peerA"/"to_peer.md").write_text(sentinel, encoding="utf-8")
                except Exception:
                    pass
            else:
                log_ledger(home, {"from":"PeerA","kind":"handoff-drop","route":"mailbox","reason":"low-signal-or-cooldown","chars":len(payload)})
        if events["peerB"].get("to_user"):
            txt = events["peerB"].get("to_user"," ").strip()
            try:
                eid = hashlib.sha1(txt.encode('utf-8', errors='ignore')).hexdigest()[:12]
            except Exception:
                eid = str(int(time.time()))
            try:
                log_ledger(home, {"from":"PeerB","kind":"to_user","eid": eid, "chars": len(txt)})
            except Exception:
                pass
            outbox_write(home, {"type":"to_user","peer":"PeerB","text":txt,"eid":eid})
            try:
                tsz = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                sha8 = sha256_text(txt)[:8]
                sentinel = compose_sentinel(ts=tsz, eid=eid, sha8=sha8, route="PeerB→User")
                (home/"mailbox"/"peerB"/"to_user.md").write_text(sentinel, encoding="utf-8")
            except Exception:
                pass
        if events["peerB"].get("to_peer"):
            payload = events["peerB"]["to_peer"].strip()
            try:
                log_ledger(home, {"from":"PeerB","kind":"to_peer-seen","route":"mailbox","chars":len(payload)})
            except Exception:
                pass
            events_api.ledger_events_from_payload("PeerB", payload)
            ack_receiver("PeerB", payload)
            mbox_counts["peerB"]["to_peer"] += 1
            mbox_last["peerB"]["to_peer"] = time.strftime("%H:%M:%S")
            last_event_ts["PeerB"] = time.time()
            try:
                if events_api.teach_intercept_missing_insight("PeerB", payload):
                    payload = ""
            except Exception:
                pass
            if payload:
                if should_forward(payload, "PeerB", "PeerA", policies, state, override_enabled=False):
                    wrapped = f"<FROM_PeerB>\n{payload}\n</FROM_PeerB>\n"
                    send_handoff("PeerB", "PeerA", wrapped)
                    try:
                        log_ledger(home, {"from":"PeerB","to":"PeerA","kind":"to_peer-forward","route":"mailbox","chars":len(payload)})
                    except Exception:
                        pass
                    try:
                        eid2 = hashlib.sha1(payload.encode('utf-8','ignore')).hexdigest()[:12]
                        outbox_write(home, {"type":"to_peer_summary","from":"PeerB","to":"PeerA","text": payload, "eid": eid2})
                    except Exception:
                        eid2 = str(int(time.time()))
                    try:
                        tsz = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                        sha8 = sha256_text(payload)[:8]
                        sentinel = compose_sentinel(ts=tsz, eid=eid2, sha8=sha8, route="PeerB→PeerA")
                        (home/"mailbox"/"peerB"/"to_peer.md").write_text(sentinel, encoding="utf-8")
                    except Exception:
                        pass
                else:
                    log_ledger(home, {"from":"PeerB","kind":"handoff-drop","route":"mailbox","reason":"low-signal-or-cooldown","chars":len(payload)})
        mbox_idx.save()
        write_status(deliver_paused_box['v'])
        write_queue_and_locks()

    return type('MailboxAPI', (), {'process': process})
