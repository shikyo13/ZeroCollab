/* Existing Follow evidence, kept in a reserved lane. Saved images are immutable;
 * browser frames update only while their own page is visible and not being read. */
import { useEffect, useRef, useState } from 'react';
import { sessionHeaders } from '../lib/api';
import type { Task } from '../lib/types';
import { imageNotes } from '../annotate/ImageViewer';
import type { Agents } from '../board/shared';
import { followName } from './Follow';
import { closePip, useFollow, type PipItem } from './followStore';
import './pip.css';

type Pane = {key:number; item:PipItem; pinned:boolean; held:boolean; graceUntil:number; heldCollapsed:boolean; collapsed:boolean|null; opened:boolean; viewed:boolean; updates:number};
type Frame = {src:string; received:number};

function useBrowserFrame(item:PipItem, frozen:boolean, hardFrozen:boolean, active:()=>boolean) {
  const [frame,setFrame]=useState<Frame|null>(null),activeRef=useRef(active),frozenRef=useRef(frozen);
  activeRef.current=active;frozenRef.current=frozen;
  const request=useRef<AbortController|null>(null),hasFrame=useRef(false),hardRef=useRef(hardFrozen);hardRef.current=hardFrozen;
  useEffect(()=>{if(hardFrozen || frozen&&hasFrame.current)request.current?.abort();},[frozen,hardFrozen]);
  useEffect(()=>{
    setFrame(null);hasFrame.current=false;if(item.kind!=='browser'||!item.project)return;
    let stopped=false,objectUrl='',timer=0;
    const current=()=>!stopped&&!hardRef.current&&(!frozenRef.current||!hasFrame.current)&&activeRef.current();
    const load=async()=>{
      if(current()){
        const controller=request.current=new AbortController();
        try{
          const query=new URLSearchParams({project:item.project!});if(item.page)query.set('page',item.page);
          const response=await fetch('/api/runtime/browser/frame?'+query,{headers:sessionHeaders(),signal:controller.signal});
          if(response.ok&&current()){
            const body=await response.blob();if(current()){
              const fresh=URL.createObjectURL(body);if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=fresh;
              hasFrame.current=true;setFrame({src:fresh,received:Date.now()/1000});
            }
          }
        }catch{/* Keep the last successful frame on pause, close or temporary unavailability. */}
      }
      if(!stopped)timer=window.setTimeout(load,1500);
    };
    void load();return()=>{stopped=true;clearTimeout(timer);request.current?.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[item.kind,item.project,item.page]);
  return frame;
}

function Preview({pane,collapsed,paused,agents,tasks,onChange,onClose,onOpenTask,onNew}:{pane:Pane;collapsed:boolean;paused:boolean;agents:Agents;tasks:Task[];onChange:(patch:Partial<Pane>)=>void;onClose:()=>void;onOpenTask:(id:string)=>void;onNew:()=>void}) {
  const {item}=pane,root=useRef<HTMLElement>(null),dialog=useRef<HTMLDialogElement>(null),opener=useRef<HTMLElement|null>(null);
  const [visible,setVisible]=useState(true),[original,setOriginal]=useState(false),[expanded,setExpanded]=useState(false),[now,setNow]=useState(Date.now()/1000);
  const closeRef=useRef(onClose);closeRef.current=onClose;
  const frozen=paused||pane.held||pane.pinned||expanded||now<pane.graceUntil;
  const frame=useBrowserFrame(item,frozen,paused||pane.pinned||expanded,()=>!collapsed&&visible&&!document.hidden&&!!root.current?.getClientRects().length);
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()/1000),1000);return()=>clearInterval(timer);},[]);
  useEffect(()=>{const node=root.current;if(!node)return;const observer=new IntersectionObserver(entries=>setVisible(entries[0].isIntersecting));observer.observe(node);return()=>observer.disconnect();},[]);
  useEffect(()=>{
    // Unseen/folded evidence stays available until it has actually been displayed.
    if(!pane.viewed||collapsed||!visible||paused||pane.pinned||pane.held||expanded||now<pane.graceUntil)return;
    const timer=setTimeout(()=>closeRef.current(),12000);return()=>clearTimeout(timer);
  },[item.id,pane.viewed,collapsed,visible,paused,pane.pinned,pane.held,expanded,now<pane.graceUntil]);
  useEffect(()=>{
    if(!expanded)return;dialog.current?.showModal();return()=>{dialog.current?.close();if(opener.current?.isConnected)opener.current.focus();};
  },[expanded]);
  const hold=(held:boolean)=>onChange({held,heldCollapsed:collapsed,graceUntil:held?0:Date.now()/1000+10});
  const image=item.kind==='browser'?frame?.src:original&&item.source?item.source:item.url;
  useEffect(()=>{
    const node=root.current?.querySelector<HTMLImageElement>('.fl-preview-image img');
    if(!node||pane.viewed||collapsed)return;
    let inView=false;
    const viewed=()=>{if(inView&&node.complete&&node.naturalWidth>0)onChange({viewed:true});};
    const observer=new IntersectionObserver(entries=>{inView=entries[0].isIntersecting;viewed();});
    node.addEventListener('load',viewed);observer.observe(node);
    return()=>{node.removeEventListener('load',viewed);observer.disconnect();};
  },[pane.viewed,collapsed,image]);
  const notes=item.kind==='markup'?imageNotes(item.note):null;
  const title=item.kind==='browser'?'Browser':item.kind==='markup'?'Annotation':'Screenshot';
  const time=new Date(item.time*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  const caption=item.kind==='browser'?frame?`${pane.pinned?'Pinned browser frame':'Live browser'} · received ${Math.max(0,Math.floor(now-frame.received))}s ago${pane.held||expanded?' · held while reading':''}`:'Waiting for a browser frame':`${title} · ${time}`;
  const task=item.task?tasks.find(t=>t.id===item.task):undefined;
  return <article ref={root} className={`fl-preview${collapsed?' is-collapsed':''}`} style={{'--preview-color':agents[item.actor]?.color || 'var(--accent)'} as React.CSSProperties}
    data-preview-id={item.id} aria-label={`${followName(item.actor,agents)} ${title} preview`} onPointerEnter={()=>hold(true)} onPointerLeave={()=>{if(!root.current?.contains(document.activeElement))hold(false);}}
    onFocus={()=>hold(true)} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node|null)&&!e.currentTarget.matches(':hover'))hold(false);}}>
    <header className="fl-preview-head"><strong>{followName(item.actor,agents)} · {title}</strong><div className="fl-preview-actions">
      <button className="fl-pip-btn" aria-expanded={!collapsed} onClick={()=>onChange({collapsed:!collapsed,heldCollapsed:!collapsed,opened:collapsed})}>{collapsed?'Show':'Fold'}</button>
      <button className="fl-pip-btn" aria-pressed={pane.pinned} disabled={item.kind==='browser'&&!frame&&!pane.pinned} onClick={()=>onChange({pinned:!pane.pinned})}>{pane.pinned?'Unpin':'Pin'}</button>
      <button className="fl-pip-btn" aria-label={`Close ${followName(item.actor,agents)} ${title} preview`} onClick={onClose}>Close</button>
      {!!pane.updates&&<button className="fl-pip-btn fl-preview-new" onClick={onNew}>{pane.updates} new previews</button>}
    </div><p className="fl-preview-source"><strong>{task?.title || 'Team activity'}</strong><span>{item.kind==='browser'?item.url || 'Shared browser':item.source?'Saved annotation · original available':'Saved screenshot'}</span></p></header>
    {!collapsed&&<div className="fl-preview-body">
      <p className="fl-preview-caption">{caption}</p>
      {item.kind==='markup'&&item.source&&<div className="fl-preview-actions" aria-label="Annotation version"><button className="fl-pip-btn" aria-pressed={!original} onClick={()=>setOriginal(false)}>Marked</button><button className="fl-pip-btn" aria-pressed={original} onClick={()=>setOriginal(true)}>Original</button></div>}
      {image?<button className="fl-preview-image" aria-label={`Expand ${title} image`} onClick={e=>{opener.current=e.currentTarget;setExpanded(true);}}><img src={image} alt={notes?.summary || `${title} evidence`}/></button>:<p className="fl-pip-empty">Waiting for the shared browser…</p>}
      {notes&&!original&&<ol className="fl-pip-legend">{notes.marks.map(mark=><li key={mark.number}><span className="fl-pip-key" style={{background:mark.color || 'var(--accent)'}}>{mark.number}</span>{mark.text}</li>)}</ol>}
      {task&&<button className="fl-pip-link" onClick={()=>onOpenTask(task.id)}>{task.title}</button>}
    </div>}
    {expanded&&<dialog ref={dialog} className="fl-preview-dialog" aria-label={`${title} image inspection`} onCancel={e=>{e.preventDefault();setExpanded(false);}} onClose={()=>setExpanded(false)}>
      <header><strong>{followName(item.actor,agents)} · {title}</strong><button className="fl-pip-btn" aria-label="Close image inspection" onClick={()=>setExpanded(false)}>Close</button></header>
      {image&&<img src={image} alt={notes?.summary || `${title} evidence`}/>}<p>{caption}</p>
    </dialog>}
  </article>;
}

export function FollowPip({agents,tasks,onOpenTask,onPreviewsChange,projectId}:{agents:Agents;tasks:Task[];onOpenTask:(id:string)=>void;onPreviewsChange?:(items:PipItem[])=>void;projectId:string}) {
  const snap=useFollow(),[panes,setPanes]=useState<Pane[]>([]),seen=useRef(0),wasFollowing=useRef(false),rail=useRef<HTMLElement>(null);
  useEffect(()=>{
    if(!snap.following){setPanes([]);wasFollowing.current=false;seen.current=0;return;}
    const incoming=(wasFollowing.current?snap.pips:snap.pips.slice(0,1)).filter(item=>item.id>seen.current&&(!item.project||item.project===projectId));
    wasFollowing.current=true;if(!incoming.length)return;
    seen.current=Math.max(seen.current,...incoming.map(item=>item.id));
    setPanes(previous=>{
      let next=previous.map(p=>({...p,updates:p.pinned||p.held||Date.now()/1000<p.graceUntil?p.updates+incoming.length:p.updates}));
      for(const item of [...incoming].reverse()){
        const same=next.findIndex(p=>item.kind==='browser'&&p.item.kind==='browser'&&p.item.project===item.project&&p.item.page===item.page&&!p.pinned&&!p.held&&Date.now()/1000>=p.graceUntil);
        if(same>=0){next[same]={...next[same],item,viewed:false};continue;}
        const pane:Pane={key:item.id,item,pinned:false,held:false,graceUntil:0,heldCollapsed:false,collapsed:null,opened:false,viewed:false,updates:0};
        // Incoming media never displaces a pinned/read surface. Its update chip leads to the new item.
        if (next.some(p=>p.pinned||p.held||Date.now()/1000<p.graceUntil)) next.push(pane); else next.unshift(pane);
      }
      return next;
    });
  },[snap.following,snap.pips,projectId]);
  useEffect(()=>{onPreviewsChange?.(panes.map(p=>p.item));},[panes,onPreviewsChange]);
  const close=(id:number)=>{
    const focused = rail.current?.querySelectorAll<HTMLElement>('.fl-preview');
    const removed = [...focused || []].find(node => node.dataset.previewId === String(id) && node.contains(document.activeElement));
    setPanes(previous=>previous.filter(p=>p.item.id!==id));closePip(id);
    if (removed) requestAnimationFrame(()=>{(rail.current?.querySelector<HTMLElement>('button') || document.querySelector<HTMLElement>('.fl-toggle'))?.focus();});
  };
  if(!snap.following||!panes.length)return null;
  return <aside ref={rail} className="fl-preview-rail" aria-label="Team previews"><h2>Team previews</h2>{panes.map((pane,index)=>{
    const collapsed=pane.collapsed ?? (pane.held||Date.now()/1000<pane.graceUntil?pane.heldCollapsed:index>=2&&!pane.pinned&&!pane.opened);
    const updates=panes.filter(p=>p.item.id>pane.item.id).length;
    return <Preview key={pane.key} pane={{...pane,updates:pane.pinned||pane.held||Date.now()/1000<pane.graceUntil?updates:0}} collapsed={collapsed} paused={snap.paused} agents={agents} tasks={tasks} onOpenTask={onOpenTask}
      onChange={patch=>setPanes(previous=>previous.map(p=>p.item.id===pane.item.id?{...p,...patch}:p))} onClose={()=>close(pane.item.id)}
      onNew={()=>{
        const newest=Math.max(...panes.map(p=>p.item.id));
        setPanes(previous=>previous.map(p=>({...p,updates:0,...p.item.id===newest?{collapsed:false,opened:true,heldCollapsed:false}:{}})));
        requestAnimationFrame(()=>rail.current?.querySelector<HTMLElement>(`[data-preview-id="${newest}"]`)?.scrollIntoView({block:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'}));
      }}/>;
  })}</aside>;
}
