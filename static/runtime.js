/* Room controls, agent approvals, live streaming bubbles and tool activity.
 * Projects, agent profiles and model settings live in the React workspace. */
(() => {
    const panel = document.createElement('section');
    panel.id = 'runtime-panel';
    panel.innerHTML = '<div class="runtime-controls"><strong>Project room</strong><button id="room-stop">Stop all</button><button id="room-resume">Resume</button><button id="room-changes">Changes</button><span id="room-mode"></span></div><div id="room-approvals"></div><details><summary>Tool activity</summary><pre id="room-tools">No tool activity yet.</pre></details>';
    document.querySelector('#app header').after(panel);
    const addressTeam = document.createElement('button');
    addressTeam.type = 'button'; addressTeam.textContent = 'Address team';
    addressTeam.className = 'ghost-btn'; addressTeam.title = 'Add @all to address the available agents';
    document.getElementById('mention-toggles-row').insertBefore(addressTeam, document.getElementById('clear-chat-btn'));
    addressTeam.onclick = () => {
        const input = document.getElementById('input');
        if (!/@all(?![\w-])/i.test(input.value)) input.value = '@all ' + input.value;
        input.dispatchEvent(new Event('input', {bubbles: true})); input.focus();
    };
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
    const deliveryLabels = {queued: 'Update queued', sending: 'Sending update', sent: 'Update sent; awaiting provider',
        accepted: 'Provider accepted update', queued_for_next_turn: 'Update queued for next turn',
        cancelled: 'Update cancelled by Stop', not_delivered_failed: 'Update not delivered; agent failed',
        superseded: 'Update superseded'};
    const deliveries = new Map();
    function refreshDelivery() {
        steeringNotice.textContent = [...deliveries.values()].filter(d => d.channel === window.activeChannel)
            .map(d => d.agent + ': ' + (deliveryLabels[d.status] || d.status)).join(' · ');
        steeringNotice.hidden = !steeringNotice.textContent;
        steeringNotice.title = 'Provider acceptance confirms delivery, not that the agent has acted on the instruction.';
    }
    function showDelivery(d) {
        deliveries.set(d.channel + ':' + d.agent, d);
        refreshDelivery();
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
            showDelivery({...update, agent: run.agent, channel: run.channel});
        const capacity = s.limits.parallel_agents_effective ?? (s.limits.parallel_agents || Object.keys(s.agents).length);
        document.getElementById('room-mode').textContent = (s.paused ? 'Stopped' : 'Ready') + ' - ' + capacity + ' agents can run together' + (s.limits.parallel_agents ? '' : ' (whole team)') + ', ' + (s.limits.chat_handoffs ? s.limits.chat_handoffs + ' chat handoffs' : 'no handoff limit') + (s.automatic_tool_approvals ? ' - automatic tool approvals' : '');
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
