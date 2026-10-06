(()=>{
 const root=document.getElementById('root'),host=document.getElementById('guide-content');if(!root||!host)return;
 const api=document.querySelector('meta[name="guide-api"]')?.content||'https://crm.pro.az/api/guides/public';
 const origin=new URL(api,location.href).origin;let articles=[],catalog=[],busy=false,pending=false;
 const cacheKey='bein-guide-published:'+api;
 try{const saved=JSON.parse(localStorage.getItem(cacheKey)||'null');if(Array.isArray(saved?.items))articles=saved.items}catch{}
 const original=new Map();
 const parentOf=slug=>slug.slice(0,slug.lastIndexOf('/'));
 function imageUrl(value){if(/^\/api\/guides\/public\/images\/[a-f0-9]{64}\.(jpg|png)$/.test(value||''))return origin+value;if(/^\/images\/articles\/[a-f0-9]{64}\.(jpg|png)$/.test(value||''))return value;return null}
 function render(){
  const route=location.pathname,row=articles.find(item=>item.slug===route);let article=root.querySelector('article');
  if(row){
   const signature=JSON.stringify(row);
   if(!article){root.replaceChildren();const wrapper=document.createElement('div');wrapper.className='container';article=document.createElement('article');wrapper.append(article);root.append(wrapper)}
   if(article.dataset.cmsSignature!==signature){
    if(!original.has(route))original.set(route,{html:article.innerHTML,title:document.title});
    const legacy=[...article.querySelectorAll('img:not([data-cms-photo])')].map(image=>image.cloneNode(true));article.replaceChildren();article.dataset.cmsSignature=signature;
    const back=document.createElement('a');back.href=parentOf(route)==='/articles'?'/':parentOf(route)||'/';back.textContent='← Bölməyə qayıt';back.style.fontSize='14px';
    const title=document.createElement('h2');title.textContent=row.title;
    const text=document.createElement('p');text.textContent=row.body;text.style.whiteSpace='pre-wrap';article.append(back,title,text,...legacy);document.title=row.title;
    for(const photo of row.images||[]){const url=imageUrl(photo.src);if(!url)continue;const figure=document.createElement('figure'),image=document.createElement('img');image.src=url;image.alt=photo.alt||row.title;image.loading='lazy';image.dataset.cmsPhoto='true';figure.append(image);if(photo.alt){const caption=document.createElement('figcaption');caption.textContent=photo.alt;caption.style.cssText='font-size:13px;color:#59748c;margin-bottom:20px';figure.append(caption)}article.append(figure)}
   }
  }else if(article?.dataset.cmsSignature&&original.has(route)){const saved=original.get(route);article.innerHTML=saved.html;delete article.dataset.cmsSignature;document.title=saved.title}
  const known=new Map(catalog.map(item=>[item.url,{slug:item.url,title:item.title}]));for(const item of articles)known.set(item.slug,item);
  const existing=new Set([...root.querySelectorAll('a[href]')].map(a=>new URL(a.href).pathname));
  const children=[...known.values()].filter(item=>parentOf(item.slug)===(route==='/'?'':route)||(route==='/'&&parentOf(item.slug)==='/articles')).filter(item=>!existing.has(item.slug));
  const key=route+'|'+JSON.stringify(children.map(item=>[item.slug,item.title]));const old=document.getElementById('cms-child-links');if(old?.dataset.key===key)return;old?.remove();
  if(children.length){const section=document.createElement('section');section.id='cms-child-links';section.dataset.key=key;section.className='container';section.style.paddingTop='30px';const title=document.createElement('h2');title.textContent='Təlimatlar';section.append(title);for(const item of children){const link=document.createElement('a');link.href=item.slug;link.textContent=item.title+' ↗';link.style.cssText='display:block;font-size:16px;padding:17px 20px;margin:12px 0;background:white;border:1px solid #d3e2ef;border-radius:12px';section.append(link)}host.append(section)}
 }
 function schedule(){if(pending)return;pending=true;queueMicrotask(()=>{pending=false;render()})}
 new MutationObserver(schedule).observe(root,{childList:true,subtree:true});
 const search=document.getElementById('guide-query'),results=document.getElementById('guide-results');
 search?.addEventListener('input',()=>{const query=search.value.trim().toLocaleLowerCase('az');if(!query)return;for(const link of [...results.querySelectorAll('a')]){const row=articles.find(item=>item.slug===new URL(link.href).pathname);if(row){if(!row.title.toLocaleLowerCase('az').includes(query))link.remove();else link.textContent=row.title+' ↗'}}const existing=new Set([...results.querySelectorAll('a')].map(a=>new URL(a.href).pathname));for(const item of articles){if(existing.has(item.slug)||!item.title.toLocaleLowerCase('az').includes(query))continue;results.querySelector('p')?.remove();const link=document.createElement('a');link.href=item.slug;link.textContent=item.title+' ↗';results.append(link)}});
 async function refresh(){if(busy)return;busy=true;try{const response=await fetch(api,{credentials:'omit',cache:'no-store',signal:AbortSignal.timeout(12000)});if(!response.ok)throw Error();const data=await response.json();if(!data.success||!Array.isArray(data.items))throw Error();articles=data.items;try{localStorage.setItem(cacheKey,JSON.stringify({items:articles}))}catch{}schedule()}catch{/* Keep original documentation and last successful published content available. */}finally{busy=false}}
 Promise.all([fetch('/guide-search.json').then(response=>response.json()).then(data=>{catalog=data;schedule()}).catch(()=>{}),fetch('/guide-articles.json').then(response=>response.ok?response.json():null).then(data=>{if(data?.items&&!articles.length){articles=data.items;try{localStorage.setItem(cacheKey,JSON.stringify({items:articles}))}catch{}schedule()}}).catch(()=>{})]).finally(refresh);

})();
