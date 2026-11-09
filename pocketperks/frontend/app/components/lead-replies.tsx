"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../lib/auth";
import SuppressionManager from "./suppression-manager";

type Reply = {
  id: string;
  lead_id: string | null;
  campaign_id: string | null;
  from_email: string;
  sender_account_email?: string | null;
  subject: string;
  body: string;
  received_at: string;
  status: "new" | "read";
  opted_out?: boolean;
  leads?: { business_name?: string; email?: string } | null;
  campaigns?: { name?: string } | null;
};

type Props = { apiBase: string; canManageSuppressions?: boolean };
type ReadFilter = "all" | "unread" | "read";

function splitQuotedMessage(body: string) {
  const lines = body.replace(/\r\n?/g, "\n").split("\n");
  const marker = lines.findIndex(line =>
    /^\s*On .{3,220}wrote:\s*$/i.test(line) ||
    /^\s*-{2,}\s*Original Message\s*-{2,}\s*$/i.test(line) ||
    /^\s*From:\s+.+$/i.test(line) || /^\s*>/.test(line) || /^\s*_{5,}\s*$/.test(line));
  if (marker < 0) return { reply: body.trim(), quoted: "" };
  return { reply: lines.slice(0, marker).join("\n").trim(), quoted: lines.slice(marker).join("\n").trim() };
}

export default function LeadReplies({ apiBase, canManageSuppressions = false }: Props) {
  const [replies, setReplies] = useState<Reply[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [readFilter, setReadFilter] = useState<ReadFilter>("all");
  const [campaignFilter, setCampaignFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState("all");
  const [openId, setOpenId] = useState("");
  const [optOutBusy, setOptOutBusy] = useState("");
  const [deleteBusy, setDeleteBusy] = useState("");
  const [releaseBusy, setReleaseBusy] = useState("");
  const [releaseReplyId, setReleaseReplyId] = useState("");
  const [releaseReason, setReleaseReason] = useState("");
  const [releaseConsent, setReleaseConsent] = useState(false);
  const [notice, setNotice] = useState("");
  const [replyDrafts, setReplyDrafts] = useState<Record<string, string>>({});
  const [replyFeedback, setReplyFeedback] = useState<Record<string, { kind: "success" | "error"; text: string }>>({});
  const [sendReplyBusy, setSendReplyBusy] = useState("");

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
      if (!response.ok) throw new Error(data.detail || "Could not refresh replies.");
      await loadReplies(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not refresh replies.");
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

  async function markOptOut(reply: Reply) {
    const address = reply.leads?.email || reply.from_email;
    if (!window.confirm(`Add ${address} to the do-not-email list? Future imports and campaigns will skip this address.`)) return;
    setOptOutBusy(reply.id);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`${apiBase}/api/lead-replies/${reply.id}/opt-out`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not record the opt-out.");
      setReplies(current => current.map(item => item.id === reply.id ? { ...item, opted_out: true } : item));
      setNotice(`${data.email || address} was added to the do-not-email list.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not record the opt-out.");
    } finally {
      setOptOutBusy("");
    }
  }

  async function releaseOptOut(reply: Reply) {
    const address = reply.leads?.email || reply.from_email;
    setReleaseBusy(reply.id);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`${apiBase}/api/suppressions/${encodeURIComponent(address)}/release`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: releaseReason, recipient_confirmed: releaseConsent }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not re-enable this address.");
      setReleaseReplyId("");
      setReleaseReason("");
      setReleaseConsent(false);
      setNotice(`${address} is allowed for future campaigns. It will not be added to a campaign that is already running.`);
      await loadReplies(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not re-enable this address.");
    } finally {
      setReleaseBusy("");
    }
  }

  async function deleteLeadReply(reply: Reply) {
    if (!window.confirm(`Permanently delete this reply from this workspace and the connected inbox? The recipient's mailbox copy will remain.`)) return;
    setDeleteBusy(reply.id);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`${apiBase}/api/lead-replies/${reply.id}`, { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not delete this reply.");
      setReplies(current => current.filter(item => item.id !== reply.id));
      if (openId === reply.id) setOpenId("");
      setNotice("Reply deleted from this workspace and the connected inbox.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete this reply.");
    } finally {
      setDeleteBusy("");
    }
  }

  async function sendReply(reply: Reply) {
    const body = (replyDrafts[reply.id] || "").trim();
    if (!body) return;
    setSendReplyBusy(reply.id); setError(""); setNotice("");
    setReplyFeedback(current => ({ ...current, [reply.id]: { kind: "success", text: "Sending your reply…" } }));
    try {
      const response = await apiFetch(`${apiBase}/api/lead-replies/${reply.id}/reply`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not send your reply.");
      setReplyDrafts(current => ({ ...current, [reply.id]: "" }));
      setReplyFeedback(current => ({ ...current, [reply.id]: { kind: "success", text: "Reply sent in the existing email conversation." } }));
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Could not send reply.";
      setError(message);
      setReplyFeedback(current => ({ ...current, [reply.id]: { kind: "error", text: message } }));
    } finally { setSendReplyBusy(""); }
  }

  const visibleReplies = useMemo(() => replies.filter(reply => {
    if (readFilter === "unread" && reply.status !== "new") return false;
    if (readFilter === "read" && reply.status !== "read") return false;
    if (campaignFilter !== "all" && reply.campaign_id !== campaignFilter) return false;
    if (dateFilter !== "all") {
      const cutoff = Date.now() - Number(dateFilter) * 24 * 60 * 60 * 1000;
      if (new Date(reply.received_at).getTime() < cutoff) return false;
    }
    const query = search.trim().toLowerCase();
    return !query || [reply.from_email, reply.subject, splitQuotedMessage(reply.body).reply, reply.leads?.business_name,
      reply.leads?.email, reply.campaigns?.name].some(value => value?.toLowerCase().includes(query));
  }), [replies, search, readFilter, campaignFilter, dateFilter]);
  const unreadCount = replies.filter(reply => reply.status === "new").length;
  const filtersActive = Boolean(search || readFilter !== "all" || campaignFilter !== "all" || dateFilter !== "all");
  const campaignOptions = Array.from(new Map(replies.filter(reply => reply.campaign_id && reply.campaigns?.name)
    .map(reply => [reply.campaign_id!, reply.campaigns!.name!])).entries());

  return (
    <section className="content lead-replies-page" aria-labelledby="replies-heading">
      <small className="eyebrow">EMAIL INBOX</small>
      <div className="title replies-heading">
        <div><h1 id="replies-heading">Lead Replies</h1>
          <p>Incoming replies from your campaigns, all in one PocketPerks workspace.</p></div>
        <button className="action-button" type="button" disabled={syncing} onClick={() => void refreshReplies()}>
          {syncing ? "Checking for replies…" : "↻ Refresh replies"}
        </button>
      </div>
      <div className="reply-summary">
        <article><small>Replies loaded</small><b>{replies.length}</b></article>
        <article><small>Unread loaded</small><b>{unreadCount}</b></article>
          <span>New replies are checked automatically while the workspace is running.</span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status" className="success">{notice}</p>}
      {canManageSuppressions && <SuppressionManager apiBase={apiBase}/>}
      <section className="card replies-card">
        <div className="replies-toolbar">
          <input aria-label="Search replies" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search sender, business, campaign or reply" />
          <div className="reply-filter-row">
            <label><span>Read status</span><select aria-label="Filter by read status" value={readFilter} onChange={event => setReadFilter(event.target.value as ReadFilter)}>
              <option value="all">All replies</option><option value="unread">Unread</option><option value="read">Read</option>
            </select></label>
            <label><span>Campaign</span><select aria-label="Filter by campaign" value={campaignFilter} onChange={event => setCampaignFilter(event.target.value)}>
              <option value="all">All campaigns</option>{campaignOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select></label>
            <label><span>Received</span><select aria-label="Filter by received date" value={dateFilter} onChange={event => setDateFilter(event.target.value)}>
              <option value="all">Any time</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option>
            </select></label>
            {filtersActive && <button type="button" className="reply-clear-filters" onClick={() => {
              setSearch(""); setReadFilter("all"); setCampaignFilter("all"); setDateFilter("all");
            }}>Clear filters</button>}
          </div>
        </div>
        <div className="reply-results-count">Showing {visibleReplies.length} of {replies.length} loaded replies</div>
        {loading ? <p className="reply-empty">Loading replies…</p> : visibleReplies.length === 0 ? (
          <div className="reply-empty"><span>↩</span><h2>{replies.length ? "No replies match these filters" : "No replies yet"}</h2>
            <p>{replies.length ? "Try a different search or show all replies." : "Replies to your campaigns will appear here when they arrive."}</p>
            {!replies.length && <button type="button" className="secondary-button" disabled={syncing} onClick={() => void refreshReplies()}>{syncing ? "Checking…" : "Check for replies"}</button>}
          </div>
        ) : <><div className="reply-list">{visibleReplies.map(reply => {
          const expanded = openId === reply.id;
          const { reply: cleanBody, quoted } = splitQuotedMessage(reply.body);
          return <article className={`reply-item ${reply.status === "new" ? "unread" : ""}`} key={reply.id}>
            <button className="reply-item-button" type="button" aria-expanded={expanded} onClick={() => {
              setOpenId(current => current === reply.id ? "" : reply.id);
              void markRead(reply);
            }}>
              <span className="reply-status-dot" aria-label={reply.status === "new" ? "Unread" : "Read"} />
              <span className="reply-main"><b>{reply.leads?.business_name || (reply.lead_id ? reply.from_email : `Unmatched reply · ${reply.from_email}`)}</b>
                <small>{reply.from_email}{reply.campaigns?.name ? ` · ${reply.campaigns.name}` : ""}</small>
                <strong>{reply.subject || "(no subject)"}</strong>
                <span>{(cleanBody || "No new text detected; quoted email available below.").replace(/\s+/g, " ").slice(0, 180)}</span>
                {reply.opted_out && <small className="reply-opt-out-badge">Opted out · future campaigns blocked</small>}</span>
              <time dateTime={reply.received_at}>{new Date(reply.received_at).toLocaleString()}</time>
            </button>
            {expanded && <div className="reply-expanded"><div className="reply-meta"><span>From <b>{reply.from_email}</b></span>
              {reply.leads?.email && <span>Lead <b>{reply.leads.email}</b></span>}{reply.campaigns?.name && <span>Campaign <b>{reply.campaigns.name}</b></span>}
            </div>{reply.opted_out && <p className="reply-opt-out-badge">Opted out · future campaigns are blocked</p>}<h3>{reply.subject || "(no subject)"}</h3><div className="reply-body-card"><p>{cleanBody || "No new reply text was detected."}</p></div>
              {!reply.opted_out && <form className="reply-compose" onSubmit={event => { event.preventDefault(); void sendReply(reply); }}>
                <label htmlFor={`reply-compose-${reply.id}`}>Reply in this conversation</label>
                <textarea id={`reply-compose-${reply.id}`} rows={4} maxLength={20000} value={replyDrafts[reply.id] || ""}
                  onChange={event => setReplyDrafts(current => ({ ...current, [reply.id]: event.target.value }))}
                  placeholder="Write a reply to this email thread…" disabled={sendReplyBusy === reply.id}/>
                <div><small>Sent from {reply.sender_account_email || "the original connected mailbox"}</small>
                  <button type="submit" className="action-button" disabled={sendReplyBusy === reply.id || !replyDrafts[reply.id]?.trim()}>
                    {sendReplyBusy === reply.id ? "Sending…" : "Send reply"}</button></div>
                {replyFeedback[reply.id] && <p role={replyFeedback[reply.id].kind === "error" ? "alert" : "status"}
                  className={replyFeedback[reply.id].kind === "error" ? "error" : "success"}>{replyFeedback[reply.id].text}</p>}
              </form>}
              <div className="reply-actions"><button type="button" className="secondary-button reply-delete-button" disabled={deleteBusy === reply.id} onClick={() => void deleteLeadReply(reply)} aria-label="Delete reply from this workspace and connected inbox"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 13h10l-1-13M9 7V4h6v3"/></svg>{deleteBusy === reply.id ? "Deleting…" : "Delete reply"}</button>
                <button type="button" className="secondary-button" disabled={reply.opted_out || optOutBusy === reply.id} onClick={() => void markOptOut(reply)}>{reply.opted_out ? "Opt-out recorded" : optOutBusy === reply.id ? "Saving opt-out…" : "Mark as opt-out"}</button>
                {canManageSuppressions && reply.opted_out && <button type="button" className="secondary-button" disabled={releaseBusy === reply.id} onClick={() => {
                  setReleaseReplyId(current => current === reply.id ? "" : reply.id);
                  setReleaseReason(""); setReleaseConsent(false); setError(""); setNotice("");
                }}>{releaseBusy === reply.id ? "Re-enabling…" : releaseReplyId === reply.id ? "Cancel re-enable" : "Re-enable for future campaigns"}</button>}</div>
              {canManageSuppressions && releaseReplyId === reply.id && <form className="suppression-release-form reply-release-form" onSubmit={event => { event.preventDefault(); void releaseOptOut(reply); }}>
                <h3>Re-enable {reply.leads?.email || reply.from_email}</h3>
                <p>Only continue if the recipient explicitly asked to receive PocketPerks emails again. This allows future campaigns, but does not change a campaign that is already running.</p>
                <label>Audit reason<textarea required minLength={10} maxLength={300} value={releaseReason} onChange={event => setReleaseReason(event.target.value)} placeholder="How and when did the recipient confirm re-subscription?" rows={2}/></label>
                <label className="suppression-consent"><input type="checkbox" checked={releaseConsent} onChange={event => setReleaseConsent(event.target.checked)}/>
                  The recipient explicitly requested to receive PocketPerks emails again.</label>
                <div><button type="button" className="secondary-button" onClick={() => setReleaseReplyId("")}>Cancel</button>
                  <button type="submit" className="action-button" disabled={releaseBusy === reply.id || !releaseConsent || releaseReason.trim().length < 10}>{releaseBusy === reply.id ? "Saving…" : "Re-enable email"}</button></div>
              </form>}
              {quoted && <details className="reply-quoted"><summary>Show quoted email</summary><pre>{quoted}</pre></details>}</div>}
          </article>;
        })}</div>{hasMore && !search && <div className="reply-load-more"><button className="secondary-button" type="button" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? "Loading…" : "Load more replies"}</button></div>}</>}
      </section>
      <p className="reply-note">Read status here is tracked in PocketPerks and does not change the original mailbox status.</p>
    </section>
  );
}
