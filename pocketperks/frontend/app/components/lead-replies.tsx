"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../lib/auth";

type Reply = {
  id: string;
  lead_id: string | null;
  campaign_id: string | null;
  from_email: string;
  subject: string;
  body: string;
  received_at: string;
  status: "new" | "read";
  leads?: { business_name?: string; email?: string } | null;
  campaigns?: { name?: string } | null;
};

type Props = { apiBase: string };

export default function LeadReplies({ apiBase }: Props) {
  const [replies, setReplies] = useState<Reply[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [openId, setOpenId] = useState("");

  const loadReplies = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/lead-replies?limit=100&offset=0`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not load replies.");
      const rows = Array.isArray(data) ? data as Reply[] : [];
      setReplies(rows);
      setHasMore(rows.length === 100);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load replies.");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/lead-replies?limit=100&offset=${replies.length}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not load more replies.");
      const rows = Array.isArray(data) ? data as Reply[] : [];
      setReplies(current => [...current, ...rows]);
      setHasMore(rows.length === 100);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load more replies.");
    } finally {
      setLoadingMore(false);
    }
  }, [apiBase, replies.length]);

  const refreshReplies = useCallback(async () => {
    setSyncing(true);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/lead-replies/sync`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not sync replies from Instantly.");
      await loadReplies(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not sync replies from Instantly.");
    } finally {
      setSyncing(false);
    }
  }, [apiBase, loadReplies]);

  useEffect(() => {
    void loadReplies();
    void refreshReplies();
    const timer = window.setInterval(() => void refreshReplies(), 60_000);
    return () => window.clearInterval(timer);
  }, [loadReplies, refreshReplies]);

  async function markRead(reply: Reply) {
    if (reply.status === "read") return;
    try {
      const response = await apiFetch(`${apiBase}/api/lead-replies/${reply.id}/read`, { method: "PATCH" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not update reply status.");
      setReplies(current => current.map(item => item.id === reply.id ? { ...item, status: "read" } : item));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update reply status.");
    }
  }

  const visibleReplies = useMemo(() => replies.filter(reply => {
    if (unreadOnly && reply.status !== "new") return false;
    const query = search.trim().toLowerCase();
    return !query || [reply.from_email, reply.subject, reply.body, reply.leads?.business_name,
      reply.leads?.email, reply.campaigns?.name].some(value => value?.toLowerCase().includes(query));
  }), [replies, search, unreadOnly]);
  const unreadCount = replies.filter(reply => reply.status === "new").length;

  return (
    <section className="content lead-replies-page" aria-labelledby="replies-heading">
      <small className="eyebrow">INSTANTLY UNIBOX</small>
      <div className="title replies-heading">
        <div><h1 id="replies-heading">Lead Replies</h1>
          <p>Incoming replies synced from Instantly into your PocketPerks workspace.</p></div>
        <button className="action-button" type="button" disabled={syncing} onClick={() => void refreshReplies()}>
          {syncing ? "Syncing…" : "↻ Refresh replies"}
        </button>
      </div>
      <div className="reply-summary">
        <article><small>Replies loaded</small><b>{replies.length}</b></article>
        <article><small>Unread in this view</small><b>{unreadCount}</b></article>
        <span>Automatic inbox check runs every minute while the backend is online.</span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      <section className="card replies-card">
        <div className="replies-toolbar">
          <input aria-label="Search replies" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search sender, business, campaign or message" />
          <button type="button" className={unreadOnly ? "secondary-button selected" : "secondary-button"} onClick={() => setUnreadOnly(value => !value)}>
            {unreadOnly ? "Showing unread" : "Unread only"}
          </button>
        </div>
        {loading ? <p className="reply-empty">Loading replies…</p> : visibleReplies.length === 0 ? (
          <div className="reply-empty"><span>↩</span><h2>{replies.length ? "No replies match these filters" : "No replies yet"}</h2>
            <p>{replies.length ? "Try a different search or show all replies." : "Replies to your Instantly campaign will appear here after the next sync."}</p>
            {!replies.length && <button type="button" className="secondary-button" disabled={syncing} onClick={() => void refreshReplies()}>{syncing ? "Checking…" : "Check Instantly now"}</button>}
          </div>
        ) : <><div className="reply-list">{visibleReplies.map(reply => {
          const expanded = openId === reply.id;
          return <article className={`reply-item ${reply.status === "new" ? "unread" : ""}`} key={reply.id}>
            <button className="reply-item-button" type="button" aria-expanded={expanded} onClick={() => {
              setOpenId(current => current === reply.id ? "" : reply.id);
              void markRead(reply);
            }}>
              <span className="reply-status-dot" aria-label={reply.status === "new" ? "Unread" : "Read"} />
              <span className="reply-main"><b>{reply.leads?.business_name || reply.from_email}</b>
                <small>{reply.from_email}{reply.campaigns?.name ? ` · ${reply.campaigns.name}` : ""}</small>
                <strong>{reply.subject || "(no subject)"}</strong>
                <span>{reply.body.replace(/\s+/g, " ").slice(0, 180)}</span></span>
              <time dateTime={reply.received_at}>{new Date(reply.received_at).toLocaleString()}</time>
            </button>
            {expanded && <div className="reply-expanded"><div className="reply-meta"><span>From <b>{reply.from_email}</b></span>
              {reply.leads?.email && <span>Lead <b>{reply.leads.email}</b></span>}{reply.campaigns?.name && <span>Campaign <b>{reply.campaigns.name}</b></span>}
            </div><h3>{reply.subject || "(no subject)"}</h3><p>{reply.body}</p></div>}
          </article>;
        })}</div>{hasMore && !search && <div className="reply-load-more"><button className="secondary-button" type="button" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? "Loading…" : "Load more replies"}</button></div>}</>}
      </section>
      <p className="reply-note">“Unread” is tracked in PocketPerks. It does not change read status in Instantly Unibox.</p>
    </section>
  );
}
