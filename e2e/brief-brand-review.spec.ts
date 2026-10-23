import {test,expect} from '@playwright/test';

test('品牌候选可确认主品牌、英文别名和关联品牌，保存后刷新仍保留',async({page})=>{
  const item=(value:string)=>({value,blockId:'vision:vr:1:text',quote:value});
  const pending=['麦咖啡','McCafé','麦当劳','McDonald’s'].map(value=>({group:'brands',value,pageNo:1,source:'text',quote:value,reason:'存在多个品牌候选，需要确认关系'}));
  const entities:any[]=[];let revision=0;let primary=item('');
  const calls:any[]=[];const otherPosts:string[]=[];
  page.on('request',r=>{if(r.method()==='POST'&&!r.url().endsWith('/facts/review'))otherPosts.push(r.url());});
  const facts=()=>({brand:primary,brandEntities:structuredClone(entities),products:[],competitors:[],sellingPoints:[],requiredTerms:[],forbiddenTerms:[],publishRules:[],thresholds:[],freeform:[],warnings:[]});
  const run=()=>({id:`facts-${revision}`,status:'succeeded',result:{facts:facts(),pendingFacts:pending,factsComplete:!pending.length}});
  await page.route('**/api/v1/projects/project',r=>r.fulfill({json:{data:{id:'project',name:'麦咖啡品牌确认',status:'active',brand:{name:'麦咖啡'},platforms:['douyin'],revision:1}}}));
  await page.route('**/api/v1/projects/project/brief',r=>r.fulfill({json:{data:{projectId:'project',revision:1,brief:{title:'品牌Brief.pdf',revision:1,fileId:'file',sourceVersionId:'source',pageCount:1,blocks:[{id:'vision:vr:1:text',pageNo:1,text:'麦咖啡 McCafé 麦当劳 McDonald’s'}],uncoveredSources:[],visualRun:{id:'vr',status:'succeeded',snapshot:{pages:[{pageNo:1,width:1000,height:700}]}}}}}}));
  await page.route('**/brief/vision/vr/pages/1',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700"><rect width="1000" height="700" fill="#f7f4e9"/><text x="70" y="130" font-size="42" fill="#222">McCafe / McDonald’s</text><text x="70" y="230" font-size="32" fill="#555">Brand identity reference</text></svg>'}));
  await page.route('**/api/v1/projects/project/brief/facts',r=>r.fulfill({json:{data:{sourceVersionId:'source',facts:facts(),lastRun:run()}}}));
  await page.route('**/api/v1/projects/project/brief/facts/review',r=>{
    const body=r.request().postDataJSON();expect(body.baseRunId).toBe(`facts-${revision}`);
    const decision=body.decisions[0];calls.push(decision);const entry=pending[decision.itemIndex];expect(entry).toBeTruthy();
    if(decision.action==='brand_primary'){primary=item(entry.value);entities.push({name:primary,aliases:[],role:'primary'});}
    if(decision.action==='brand_related')entities.push({name:item(entry.value),aliases:[],role:'related'});
    if(decision.action==='brand_alias'){const target=entities.find(e=>e.name.value===decision.targetBrand);expect(target).toBeTruthy();target.aliases.push(item(entry.value));}
    pending.splice(decision.itemIndex,1);revision++;
    return r.fulfill({json:{data:run()}});
  });
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/projects/project/brief-review?tab=pending');
  const card=(name:string)=>page.locator('.brief-review-fact').filter({has:page.getByText(name,{exact:true})});
  await expect(card('McCafé').getByRole('button',{name:'归为别名',exact:true})).toBeDisabled();
  await page.screenshot({path:'test-results/brand-review-actions.png',fullPage:true});
  await card('麦咖啡').getByRole('button',{name:'设为主品牌',exact:true}).click();
  await expect(page.locator('.brief-review-fact')).toHaveCount(3);
  await card('McCafé').getByRole('button',{name:'归为别名',exact:true}).click();
  await expect(page.locator('.brief-review-fact')).toHaveCount(2);
  await card('麦当劳').getByRole('button',{name:'标为关联品牌',exact:true}).click();
  await expect(page.locator('.brief-review-fact')).toHaveCount(1);
  await card('McDonald’s').getByRole('combobox').click();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await card('McDonald’s').getByRole('button',{name:'归为别名',exact:true}).click();
  await expect(page.locator('.brief-review-fact')).toHaveCount(0);
  await page.getByRole('tab',{name:/事实卡/}).click();
  await expect(page.locator('.brief-brand-entities')).toContainText('主推品牌');
  await expect(page.locator('.brief-brand-entities')).toContainText('关联品牌');
  await expect(page.locator('.brief-brand-entities')).toContainText('McCafé');
  await expect(page.locator('.brief-brand-entities')).toContainText('McDonald’s');
  await page.reload();await expect(page.locator('.brief-brand-entities')).toContainText('McDonald’s');
  await page.screenshot({path:'test-results/brand-relations-confirmed.png',fullPage:true});
  expect(calls.map(d=>d.action)).toEqual(['brand_primary','brand_alias','brand_related','brand_alias']);
  expect(calls[3].targetBrand).toBe('麦当劳');expect(otherPosts).toEqual([]);expect(errors).toEqual([]);
});
