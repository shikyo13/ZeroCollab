/* Room controls, agent approvals, live streaming bubbles and tool activity.
 * Projects, agent profiles and model settings live in the React workspace. */
(() => {
    const panel = document.createElement('section');
    panel.id = 'runtime-panel';
    panel.innerHTML = '<div class="runtime-controls"><strong>Project room</strong><button id="room-stop">Stop all</button><button id="room-resume">Resume</button><button id="room-changes">Changes</button><span id="room-mode"></span></div><div id="room-approvals"></div><details><summary>Tool activity</summary><pre id="room-tools">No tool calls since this page was opened.</pre></details>';
    document.querySelector('#app header').after(panel);
    let state = null;
    const active = new Map();
    const approvalCards = new Map();
    const activity = [];
    const ACTIVITY_SHOWN = 12;           // tool calls kept in the panel
    const ACTIVITY_DETAIL_CHARS = 4000;  // detail kept per call
    const steeringNotice = document.createElement('div');
    steeringNotice.id = 'steering-delivery';
    steeringNotice.setAttribute('role', 'status');
    document.querySelector('#app footer')?.prepend(steeringNotice);
    // Steering messages sent to a running turn, tracked per request (run + message or ref), so
    // runtime_state replaying a run's recent requests never restarts an older notice. Each agent
    // shows its most recently changed request; settled states fade after the ms below, while
    // queued and sending states stay until they change.
    const deliveryLabels = {queued: 'Message queued for {a}', sending: 'Sending to {a}…', sent: 'Sending to {a}…',
        accepted: '{a} received your message', queued_for_next_turn: '{a} will see your message next turn',
        cancelled: 'Message to {a} cancelled by Stop', not_delivered_failed: 'Message to {a} not delivered: the turn failed'};
    const deliveryFade = {accepted: 4000, cancelled: 8000, superseded: 0, queued_for_next_turn: 20000, not_delivered_failed: 20000};
    const DELIVERIES_KEPT = 200;  // settled requests are remembered so a replay cannot revive them
    const deliveries = new Map();
    let deliverySeq = 0;
    const agentLabel = id => window.agentConfig?.[id]?.label || id;
    const faded = (d, now) => d.status in deliveryFade && now - d.since >= deliveryFade[d.status];
    function refreshDelivery() {
        const now = Date.now();
        const latest = new Map();  // agent -> its most recently changed request in this chat
        for (const d of deliveries.values()) {
            if (d.channel !== window.activeChannel) continue;
            if (!latest.has(d.agent) || d.seq > latest.get(d.agent).seq) latest.set(d.agent, d);
        }
        steeringNotice.textContent = [...latest.values()].filter(d => deliveryLabels[d.status] && !faded(d, now))
            .map(d => deliveryLabels[d.status].replace('{a}', agentLabel(d.agent))).join(' · ');
        steeringNotice.hidden = !steeringNotice.textContent;
        steeringNotice.title = 'Received means the agent has your message; it may not have acted on it yet.';
    }
    function showDelivery(d) {
        const key = [d.run, d.message_id ?? '', d.ref ?? ''].join(':');
        const previous = deliveries.get(key);
        if (previous && previous.status === d.status) return;  // a replay of what is already shown
        deliveries.delete(key);
        deliveries.set(key, {run: d.run, agent: d.agent, channel: d.channel, status: d.status, since: Date.now(), seq: ++deliverySeq});
        while (deliveries.size > DELIVERIES_KEPT) deliveries.delete(deliveries.keys().next().value);
        refreshDelivery();
        if (d.status in deliveryFade) setTimeout(refreshDelivery, deliveryFade[d.status] + 50);
    }
    const api = async (path, body) => {
        const response = await fetch('/api/runtime' + path, {method: body === undefined ? 'GET' : 'POST',
            headers: {'X-Session-Token': window.__SESSION_TOKEN__, 'Content-Type': 'application/json'},
            ...(body === undefined ? {} : {body: JSON.stringify(body)})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || response.statusText);
        return data;
    };
    const showError = e => { document.getElementById('room-mode').textContent = e.message; };
    function render(s) {
        state = s;
        for (const run of s.runs || []) for (const update of run.steering || [])
            showDelivery({...update, run: run.id, agent: run.agent, channel: run.channel});
        const capacity = s.limits.parallel_agents_effective ?? (s.limits.parallel_agents || Object.keys(s.agents).length);
        document.getElementById('room-mode').textContent = (s.paused ? 'Stopped' : 'Ready') + ' - ' + capacity + (capacity === 1 ? ' agent can run' : ' agents can run together') + (s.limits.parallel_agents ? '' : ' (whole team)') + ', ' + (s.limits.chat_handoffs ? s.limits.chat_handoffs + ' chat handoffs' : 'no handoff limit') + (s.automatic_tool_approvals ? ' - automatic tool approvals' : '');
        document.getElementById('room-stop').disabled = s.paused;
        document.getElementById('room-resume').disabled = !s.paused;
        for (const pending of s.approvals || []) approval(pending);
        const valid = new Set((s.approvals || []).map(a => a.id));
        for (const [id, card] of approvalCards) if (!valid.has(id)) { card.remove(); approvalCards.delete(id); }
        for (const run of s.runs || []) {
            if (run.text) { live(run.id, run.agent, run.channel, run.message); active.get(run.id).text.textContent = run.text; }
            else { const pending = active.get(run.id); if (pending) { pending.el.remove(); active.delete(run.id); } }
        }
    }
    function live(id, name, channel, message) {
        if (active.has(id) && message && active.get(id).message !== message) {
            active.get(id).el.remove(); active.delete(id);
        }
        if (active.has(id)) {
            const run = active.get(id);
            if (!run.el.isConnected) document.getElementById('messages').append(run.el);
            run.el.hidden = window.activeChannel !== run.channel;
            return;
        }
        const el = document.createElement('div'); el.className = 'message runtime-live'; el.dataset.channel = channel;
        const bubble = document.createElement('div'); bubble.className = 'chat-bubble';
        const header = document.createElement('div'); header.className = 'bubble-header'; header.textContent = name + ' · streaming in ' + (window.chatSessionTitle?.(channel) || channel);
        const text = document.createElement('div'); text.className = 'msg-text';
        bubble.append(header, text); el.append(bubble);
        document.getElementById('messages').append(el); active.set(id, {el, text, channel, message});
        if (window.activeChannel && window.activeChannel !== channel) el.hidden = true;
    }
    function approval(a) {
        if (approvalCards.has(a.id)) return;
        const el = document.createElement('article'); el.className = 'runtime-approval';
        const title = document.createElement('strong'); title.textContent = a.agent + ' needs approval: ' + a.tool;
        const reason = document.createElement('p'); reason.textContent = a.reason + '\nWorktree: ' + a.worktree;
        const params = document.createElement('pre'); params.textContent = JSON.stringify(a.params, null, 2);
        const yes = document.createElement('button'); yes.textContent = a.question ? 'Send answer' : 'Allow once';
        const no = document.createElement('button'); no.textContent = 'Deny';
        let answer;
        if (a.question) { answer = document.createElement('input'); answer.placeholder = 'Your answer'; el.append(answer); }
        const decide = async allow => {
            yes.disabled = no.disabled = true;
            try { await api('/approval/' + a.id, {allow, answer: answer?.value || ''}); el.remove(); approvalCards.delete(a.id); }
            catch(e) { showError(e); yes.disabled = no.disabled = false; }
        };
        yes.onclick = () => decide(true); no.onclick = () => decide(false);
        el.append(title, reason, params, yes, no); document.getElementById('room-approvals').append(el); approvalCards.set(a.id, el);
    }
    document.getElementById('room-stop').onclick = () => api('/stop', {}).catch(showError);
    document.getElementById('room-resume').onclick = () => api('/resume', {}).catch(showError);
    document.getElementById('room-changes').onclick = async () => {
        try {
            const diffs = await api('/diffs'); const dialog = document.createElement('dialog'); dialog.className = 'runtime-dialog';
            const close = document.createElement('button'); close.textContent = 'Close'; close.onclick = () => {dialog.close(); dialog.remove();};
            const pre = document.createElement('pre'); pre.textContent = Object.entries(diffs).map(([n,d]) => n + '\n' + (d || 'No changes')).join('\n\n');
            dialog.append(close, pre); document.body.append(dialog); dialog.showModal();
        } catch(e) {showError(e);}
    };
    Hub.on('runtime_state', e => render(e.data));
    Hub.on('runtime_steering', e => showDelivery(e.data));
    Hub.on('runtime_start', e => { const d = e.data; live(d.run, d.agent, d.channel); });
    Hub.on('runtime_delta', e => { const d = e.data; live(d.run, d.agent, d.channel, d.message); active.get(d.run).text.textContent += d.text; if(d.channel === window.activeChannel) window.followIncomingContent?.(); });
    Hub.on('runtime_message_end', e => { const run = active.get(e.data.run); if(run && (!run.message || run.message === e.data.message)) {run.el.remove(); active.delete(e.data.run);} });
    Hub.on('runtime_end', e => { const run = active.get(e.data.run); if(run) {run.el.remove(); active.delete(e.data.run);} });
    Hub.on('runtime_approval', e => approval(e.data));
    Hub.on('runtime_approval_resolved', e => { approvalCards.get(e.data.id)?.remove(); approvalCards.delete(e.data.id); });
    Hub.on('runtime_tool', e => {
        activity.unshift(e.data.agent + ': ' + e.data.tool + ' · ' + e.data.status + '\n' + JSON.stringify(e.data.details, null, 2).slice(0, ACTIVITY_DETAIL_CHARS));
        activity.length = Math.min(activity.length, ACTIVITY_SHOWN);  // only the shown entries are kept
        document.getElementById('room-tools').textContent = activity.join('\n\n');
    });
    Hub.on('history_complete', () => api('').then(render).catch(showError));
    Store.watch('activeChannel', () => {
        refreshDelivery();
        for (const run of active.values()) {
            if (!run.el.isConnected) document.getElementById('messages').append(run.el);
            run.el.hidden = window.activeChannel !== run.channel;
        }
    });
    api('').then(render).catch(showError);
})();
