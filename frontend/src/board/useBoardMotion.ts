import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';

type Position = {rect:DOMRect; status:string; node:HTMLElement};
type Flight = {id:string; from:Position};
const EASE = 'cubic-bezier(.2,0,0,1)';

/** Decorative copies travel; real cards always reflect the server and remain usable. */
export function useBoardMotion(root:RefObject<HTMLElement|null>, paused:boolean, project:string, enabled:boolean) {
  const previous=useRef(new Map<string,Position>()),queue=useRef<Flight[]>([]);
  const active=useRef(new Map<string,{ghost:HTMLElement;animation:Animation}>());
  const held=useRef(new Set<Animation>()),state=useRef({paused,enabled}),identity=useRef(project);
  state.current={paused,enabled};
  const generation=useRef(0),timer=useRef(0),nextStart=useRef(0),noticeTimer=useRef(0),[notice,setNotice]=useState('');
  const collect=()=>new Map([...(root.current?.querySelectorAll<HTMLElement>('[data-task-id]') || [])].map(node=>[node.dataset.taskId!,{node,status:node.dataset.taskStatus || '',rect:node.getBoundingClientRect()}]));
  const clear=()=>{
    ++generation.current;clearTimeout(timer.current);queue.current=[];previous.current.clear();
    for(const flight of active.current.values()){flight.animation.cancel();flight.ghost.remove();}active.current.clear();
    for(const animation of held.current)animation.cancel();held.current.clear();nextStart.current=0;
  };
  const cue=(node:HTMLElement)=>{
    node.classList.add('bd-motion-cue');setTimeout(()=>node.classList.remove('bd-motion-cue'),1400);
    if(node.dataset.taskStatus==='blocked'&&!matchMedia('(prefers-reduced-motion: reduce)').matches)
      node.animate([{transform:'translateX(0)'},{transform:'translateX(-7px)'},{transform:'translateX(6px)'},{transform:'translateX(-3px)'},{transform:'translateX(0)'}],{duration:480,easing:EASE});
  };
  const pump=()=>{
    if(state.current.paused||!state.current.enabled)return;
    const wait=nextStart.current-performance.now();
    if(wait>0){clearTimeout(timer.current);timer.current=window.setTimeout(pump,wait);return;}
    while(active.current.size<3&&queue.current.length){
      const job=queue.current.shift()!,node=root.current?.querySelector<HTMLElement>(`[data-task-id="${CSS.escape(job.id)}"]`);
      if(!node)continue;
      const to=node.getBoundingClientRect(),from=job.from.rect,board=root.current!.getBoundingClientRect();
      const visible=(r:DOMRect)=>r.width>0&&r.height>0&&r.bottom>Math.max(0,board.top)&&r.top<Math.min(innerHeight,board.bottom)&&r.right>board.left&&r.left<Math.min(innerWidth,board.right);
      if(!visible(from)||!visible(to)||node.matches(':hover, :focus-within')){
        cue(node);
        if(!visible(to)){
          setNotice(`${node.querySelector('.bd-card-title')?.textContent?.trim() || 'Task'} moved to ${node.dataset.taskStatus}.`);
          clearTimeout(noticeTimer.current);noticeTimer.current=window.setTimeout(()=>setNotice(''),3000);
        }
        continue;
      }
      const ghost=job.from.node.cloneNode(true) as HTMLElement;
      ghost.removeAttribute('data-task-id');ghost.removeAttribute('id');ghost.setAttribute('aria-hidden','true');ghost.inert=true;
      ghost.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));
      ghost.classList.add('bd-card-flight');Object.assign(ghost.style,{left:from.left+'px',top:from.top+'px',width:from.width+'px',height:from.height+'px'});document.body.append(ghost);
      const x=to.left-from.left,y=to.top-from.top,sx=to.width/from.width,sy=node.dataset.taskStatus==='done'?Math.max(.15,to.height/from.height):1;
      const animation=ghost.animate([{transform:'translate(0,0) scale(1)',opacity:.9},{transform:`translate(${x}px,${y}px) scale(${sx},${sy})`,opacity:.08}],{duration:560,easing:EASE,fill:'forwards'});
      const flight={ghost,animation},epoch=generation.current;active.current.set(job.id,flight);
      animation.finished.catch(()=>undefined).then(()=>{
        ghost.remove();if(epoch!==generation.current||active.current.get(job.id)!==flight)return;
        active.current.delete(job.id);held.current.delete(animation);
        const current=root.current?.querySelector<HTMLElement>(`[data-task-id="${CSS.escape(job.id)}"]`);if(current)cue(current);
        nextStart.current=performance.now()+120;clearTimeout(timer.current);timer.current=window.setTimeout(pump,120);
      });
    }
  };
  useLayoutEffect(()=>{
    if(identity.current!==project){clear();identity.current=project;}
    const node=root.current;if(!node||!node.getClientRects().length){clear();return;}
    const animations=[...node.getAnimations({subtree:true}),...[...active.current.values()].map(f=>f.animation)];
    if(paused){for(const a of animations)if(a.playState==='running'){a.pause();held.current.add(a);}return;}
    // Only resume animations this hook paused. Never replay a completed animation on a stream update.
    for(const a of held.current)if(a.playState==='paused')a.play();held.current.clear();
    const next=collect();
    if(!enabled){clear();previous.current=next;return;}
    const changed=[...next].filter(([id,p])=>previous.current.has(id)&&previous.current.get(id)!.status!==p.status);
    const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    if(changed.length){
      for(const [id,p] of changed){
        queue.current=queue.current.filter(job=>job.id!==id);
        const old=active.current.get(id);if(old){active.current.delete(id);held.current.delete(old.animation);old.animation.cancel();old.ghost.remove();}
        if(reduced){p.node.animate([{opacity:.6},{opacity:1}],{duration:160});continue;}
        queue.current.push({id,from:previous.current.get(id)!});p.node.classList.add('bd-motion-cue');
      }
      if(!reduced)for(const [id,p] of next){
        const before=previous.current.get(id);if(!before||before.status!==p.status||active.current.has(id)||p.node.matches(':hover, :focus-within'))continue;
        const x=before.rect.left-p.rect.left,y=before.rect.top-p.rect.top;
        if(Math.abs(x)+Math.abs(y)>1)p.node.animate([{transform:`translate(${x}px,${y}px)`},{transform:'translate(0,0)'}],{duration:320,easing:EASE});
      }
    }
    previous.current=next;pump();
  });
  useEffect(()=>{
    const scroll=()=>{if(!active.current.size&&!queue.current.length&&!state.current.paused)previous.current=collect();};
    window.addEventListener('scroll',scroll,true);return()=>{window.removeEventListener('scroll',scroll,true);clear();clearTimeout(noticeTimer.current);};
  },[]);
  return notice;
}
