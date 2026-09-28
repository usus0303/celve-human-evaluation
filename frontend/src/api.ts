let csrf='';
let studyId='';
export function setStudyId(value:string){studyId=value;}
export function setCsrf(value:string){csrf=value;}
export async function apiFetch(url:string,options:RequestInit={}){
 const headers=new Headers(options.headers);if(studyId)headers.set('X-Study-ID',studyId);if(options.method&&options.method!=='GET'&&options.method!=='HEAD')headers.set('X-CSRF-Token',csrf);
 const result=await fetch(url,{...options,headers,credentials:'same-origin'});
 if(result.status===401&&!url.endsWith('/login')&&!url.endsWith('/session'))window.dispatchEvent(new Event('celve-session-expired'));
 return result;
}
export async function apiJson(url:string,options?:RequestInit){const r=await apiFetch(url,options);const d=await r.json();if(!r.ok)throw new Error(d.error||'Request failed');return d;}
