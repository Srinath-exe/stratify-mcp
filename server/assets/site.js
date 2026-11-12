/* Stratify site: copy buttons, client tabs, docs section highlight. No dependencies. */
(function(){
  "use strict";
  function copyText(t, b){
    var done=function(){ b.textContent="Copied"; b.classList.add("ok"); setTimeout(function(){ b.textContent="Copy"; b.classList.remove("ok"); },1500); };
    if(navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done,function(){ fallback(t); done(); });
    else { fallback(t); done(); }
  }
  function fallback(t){ var ta=document.createElement("textarea"); ta.value=t; ta.style.position="fixed"; ta.style.opacity="0"; document.body.appendChild(ta); ta.select(); try{ document.execCommand("copy"); }catch(e){} ta.remove(); }
  document.addEventListener("click",function(e){
    var b=e.target.closest(".copy"); if(!b) return;
    var t=b.getAttribute("data-text"); if(t===null){ var c=b.parentNode.querySelector("pre,code"); t=c?c.textContent:""; }
    copyText(t,b);
  });
  var KEY="stratify-client";
  function pick(box,k){ var tabs=box.querySelectorAll("[data-tab]"), any=false;
    [].forEach.call(tabs,function(t){ if(t.getAttribute("data-tab")===k) any=true; }); if(!any) return false;
    [].forEach.call(tabs,function(t){ t.setAttribute("aria-selected",t.getAttribute("data-tab")===k?"true":"false"); });
    [].forEach.call(box.querySelectorAll("[data-panel]"),function(p){ p.removeAttribute("data-off"); p.hidden=p.getAttribute("data-panel")!==k; });
    return true; }
  [].forEach.call(document.querySelectorAll("[data-tabs]"),function(box){
    var saved=null; try{ saved=localStorage.getItem(KEY); }catch(e){}
    var first=box.querySelector('[aria-selected="true"]');
    if(!(saved&&pick(box,saved))) pick(box,first?first.getAttribute("data-tab"):"");
    box.addEventListener("click",function(e){ var t=e.target.closest("[data-tab]"); if(!t) return; var k=t.getAttribute("data-tab"); pick(box,k); try{ localStorage.setItem(KEY,k); }catch(err){} });
  });
  var nav=document.querySelector(".dnav"); if(nav&&"IntersectionObserver" in window){
    var links={}; [].forEach.call(nav.querySelectorAll("a"),function(a){ links[a.getAttribute("href").slice(1)]=a; });
    var io=new IntersectionObserver(function(es){ es.forEach(function(en){ if(en.isIntersecting){ var a=links[en.target.id]; if(!a) return;
      [].forEach.call(nav.querySelectorAll("a.on"),function(x){ x.classList.remove("on"); }); a.classList.add("on"); } }); },{rootMargin:"-20% 0px -70% 0px"});
    [].forEach.call(document.querySelectorAll(".doc > section[id]"),function(s){ io.observe(s); });
  }
})();
