export type Evidence={mode:'patches'|'none'|'unsure'|'outside';patches:string[]};
export type Answers={countries:string[];countryMode:'selected'|'none'|'unsure';evidence:Record<string,Evidence>;rating?:number|null;ratingUnsure?:boolean;lockedAt?:string;submittedAt?:string;language?:string;draftStep?:1|2};
export type Saved={image_id:string;stage:'draft'|'evidence'|'locked'|'complete';answers:Answers;updated_at:string};
export type Patch={patch_id:string;image:string;box_xyxy:number[]|null};
export type Item={image_id:string;original_image:string;patches:Patch[];width:number;height:number};
export type Study={countries:{id:string;name:string}[];items:Item[]};
export const emptyAnswers=():Answers=>({countries:[],countryMode:'selected',evidence:{}});
export function validateAnswers(raw:unknown,item:Item,allowed:string[],lock:boolean,draft=false):Answers {
 if(!raw || typeof raw!=='object')throw new Error('Missing answers.');
 const a=raw as Answers;
 if(!['selected','none','unsure'].includes(a.countryMode))throw new Error('Choose countries or a no-selection option.');
 if(!Array.isArray(a.countries)||a.countries.some(c=>typeof c!=='string'||!allowed.includes(c))||new Set(a.countries).size!==a.countries.length)throw new Error('Invalid country selection.');
 if(a.countryMode!=='selected'&&a.countries.length>0||!draft&&a.countryMode==='selected'&&a.countries.length===0)throw new Error('Choose at least one country, or select none / unsure.');
 const evidence:Record<string,Evidence>={};
 for(const c of a.countries){
  const e=a.evidence?.[c];
  if(!e&&!lock)continue;
  if(!e||!['patches','none','unsure','outside'].includes(e.mode)||!Array.isArray(e.patches))throw new Error('Answer the evidence question for every selected country.');
  if(e.patches.some(p=>typeof p!=='string'||!item.patches.some(x=>x.patch_id===p))||new Set(e.patches).size!==e.patches.length)throw new Error('Invalid patch selection.');
  if(e.mode!=='patches'&&e.patches.length>0||lock&&e.mode==='patches'&&e.patches.length===0)throw new Error('Select patches or one of the alternative answers.');
  evidence[c]={mode:e.mode,patches:[...e.patches]};
 }
 return {countries:[...a.countries],countryMode:a.countryMode,evidence,language:a.language==='ko'?'ko':'en'};
}

export function resumeStep(row?:Saved):number {
 if(row?.stage==='locked'||row?.stage==='complete')return 3;
 return row?.answers.draftStep??(row?.stage==='evidence'?2:1);
}

export function nextUnfinished(items:Item[],saved:Saved[],after=-1):number {
 for(let offset=1;offset<=items.length;offset++){
  const index=(after+offset)%items.length;
  if(!saved.some(r=>r.image_id===items[index].image_id&&r.stage==='complete'))return index;
 }
 return -1;
}
