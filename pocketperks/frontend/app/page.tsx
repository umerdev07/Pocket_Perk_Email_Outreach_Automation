const stages = [
  {
    number: "01",
    title: "Prepare your campaign",
    description: "Set the audience, message context, sending window and follow-up timing in one place.",
    icon: "✳",
  },
  {
    number: "02",
    title: "Personalize and review",
    description: "Create relevant drafts for each business, check the wording, and approve messages before they proceed.",
    icon: "✎",
  },
  {
    number: "03",
    title: "Send with control",
    description: "Sync approved leads to Instantly and let the selected inboxes follow the campaign schedule.",
    icon: "↗",
  },
  {
    number: "04",
    title: "Manage every reply",
    description: "Keep replies, opt-outs and follow-up actions together in the PocketPerks workspace.",
    icon: "↩",
  },
];

const faqs = [
  {
    question: "Does PocketPerks send messages without review?",
    answer: "The workflow supports review and approval before messages are queued. Sending then follows the connected campaign settings in Instantly.",
  },
  {
    question: "Can I work with different kinds of local businesses?",
    answer: "Yes. Campaign context and verified lead details guide the message for cafés, restaurants, barbers and other suitable local businesses.",
  },
  {
    question: "Where do replies and opt-outs go?",
    answer: "Replies sync into Lead Replies. Opted-out addresses are recorded in the suppression list so future campaign imports and syncs can be blocked.",
  },
  {
    question: "Is the demo video available yet?",
    answer: "The demo area is reserved for the product walkthrough and can be filled with the approved video when it is ready.",
  },
];

export default function LandingPage() {
  return (
    <main className="landing-page">
      <header className="landing-nav">
        <a className="landing-brand" href="#top" aria-label="PocketPerks home">
          <span className="landing-brand-mark">P</span>
          <span>PocketPerks<small>OUTREACH WORKSPACE</small></span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#how-it-works">How it works</a>
          <a href="#demo">Demo</a>
          <a href="#faq">FAQ</a>
        </nav>
        <a className="landing-login" href="/login">Log in <span aria-hidden="true">↗</span></a>
      </header>

      <section className="landing-hero" id="top">
        <div className="landing-hero-copy">
          <div className="landing-eyebrow"><span /> POCKETPERKS OUTREACH</div>
          <h1>Good local relationships start with a <em>thoughtful hello.</em></h1>
          <p>Plan your outreach, personalize each email, and keep every review, send and reply in one calm workspace.</p>
          <div className="landing-hero-actions">
            <a className="landing-primary" href="/login">Sign in to your workspace <span aria-hidden="true">→</span></a>
            <a className="landing-text-link" href="#how-it-works">See how it works <span aria-hidden="true">↓</span></a>
          </div>
          <div className="landing-trust-line"><span className="landing-check">✓</span> Built around human review and clear sending controls</div>
        </div>
        <div className="landing-hero-art" aria-label="Campaign workflow preview">
          <div className="landing-orbit landing-orbit-one" />
          <div className="landing-orbit landing-orbit-two" />
          <div className="landing-art-spark spark-one">✳</div>
          <div className="landing-art-spark spark-two">✦</div>
          <div className="landing-preview-card">
            <div className="landing-preview-top"><span className="landing-preview-dot" /> CAMPAIGN OVERVIEW <span className="landing-preview-menu">···</span></div>
            <div className="landing-preview-title">Local business introduction</div>
            <div className="landing-preview-subtitle">A clear path from first draft to reply</div>
            <div className="landing-preview-flow">
              <div className="preview-flow-row"><span className="preview-flow-icon mint">✳</span><span><b>Personalized drafts</b><small>Ready for a human review</small></span><i>✓</i></div>
              <div className="preview-flow-row"><span className="preview-flow-icon lilac">↗</span><span><b>Sending schedule</b><small>Managed through Instantly</small></span><i>✓</i></div>
              <div className="preview-flow-row"><span className="preview-flow-icon peach">↩</span><span><b>Replies in one place</b><small>Follow up with context</small></span><i className="preview-live">LIVE</i></div>
            </div>
            <div className="landing-preview-footer"><span><b /> Your workflow, at a glance</span><span>01 — 04</span></div>
          </div>
          <div className="landing-float-note"><span>✦</span><div><b>Every message has context</b><small>Relevant to the business</small></div></div>
        </div>
        <div className="landing-hero-bottom"><span>CAMPAIGN</span><i /> <span>PERSONALIZATION</span><i /> <span>REPLIES</span><span className="landing-hero-caption">A considered way to introduce PocketPerks</span></div>
      </section>

      <section className="landing-intro" aria-label="Product summary">
        <div><span className="landing-section-kicker">ONE CONNECTED WORKSPACE</span><h2>From a good first draft to a clear next step.</h2></div>
        <p>PocketPerks brings campaign planning, personalized email preparation, approval, Instantly sending and reply management into one straightforward process.</p>
      </section>

      <section className="landing-stages" id="how-it-works">
        <div className="landing-section-heading"><div><span className="landing-section-kicker">HOW IT WORKS</span><h2>Four clear stages.<br /><em>One smooth workflow.</em></h2></div><p>Stay in control at every step, from the first lead file to the conversation that follows.</p></div>
        <div className="landing-stage-grid">
          {stages.map((stage) => <article className="landing-stage-card" key={stage.number}>
            <div className="landing-stage-top"><span>{stage.number}</span><i>{stage.icon}</i></div>
            <h3>{stage.title}</h3>
            <p>{stage.description}</p>
            <div className="landing-stage-rule"><span style={{ width: `${Number(stage.number) * 25}%` }} /></div>
          </article>)}
        </div>
      </section>

      <section className="landing-demo" id="demo">
        <div className="landing-demo-copy"><span className="landing-section-kicker">TAKE A LOOK AROUND</span><h2>A quick tour,<br /><em>coming into view.</em></h2><p>See how a campaign moves through PocketPerks—from planning and review to replies and opt-outs.</p><a className="landing-demo-login" href="/login">Open the workspace <span aria-hidden="true">→</span></a></div>
        <div className="landing-video-placeholder" aria-label="Reserved space for the PocketPerks demo video">
          <div className="landing-video-frame"><div className="landing-video-glow" /><span className="landing-play">▶</span><div className="landing-video-label"><span>PRODUCT WALKTHROUGH</span><b>Demo video will appear here</b><small>Campaigns · Email review · Instantly · Replies</small></div><div className="landing-video-corner">PP / 01</div></div>
        </div>
      </section>

      <section className="landing-faq" id="faq">
        <div className="landing-faq-heading"><span className="landing-section-kicker">GOOD TO KNOW</span><h2>Questions, <em>answered.</em></h2><p>A few useful details about the PocketPerks outreach workflow.</p></div>
        <div className="landing-faq-list">{faqs.map((faq) => <details key={faq.question}><summary>{faq.question}<span>+</span></summary><p>{faq.answer}</p></details>)}</div>
      </section>

      <section className="landing-cta"><div><span className="landing-section-kicker">YOUR TEAM WORKSPACE</span><h2>Ready when you are.</h2><p>Sign in to prepare your next PocketPerks campaign.</p></div><a className="landing-primary" href="/login">Log in to PocketPerks <span aria-hidden="true">→</span></a><div className="landing-cta-orb" /></section>

      <footer className="landing-footer">
        <a className="landing-brand" href="#top"><span className="landing-brand-mark">P</span><span>PocketPerks<small>OUTREACH WORKSPACE</small></span></a>
        <p>Thoughtful outreach, managed with care.</p>
        <nav aria-label="Footer navigation"><a href="#how-it-works">How it works</a><a href="#demo">Demo</a><a href="#faq">FAQ</a><a href="/login">Log in</a></nav>
        <small className="landing-copyright">© {new Date().getFullYear()} PocketPerks · VenturesSky Ltd</small>
      </footer>
    </main>
  );
}
