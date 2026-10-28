import { test, expect } from '@playwright/test';
test('三类规则共用生成卡片，图文生成只更新图文草稿（固定响应）', async ({ page, request }) => {
  const headers=()=>({'Idempotency-Key':crypto.randomUUID()});
  const p=(await (await request.post('/api/v1/projects',{headers:headers(),data:{name:'统一生成入口',brandName:'品牌A',platforms:['xiaohongshu']}})).json()).data;
  let calls=0;
  await page.route('**/api/v1/settings/models/decomposition',r=>r.fulfill({json:{data:{hasKey:true,revision:1}}}));
  await page.route(`**/api/v1/projects/${p.id}/brief`,r=>r.fulfill({json:{data:{brief:{title:'测试Brief',sourceVersionId:'fixed'}}}}));
  await page.route(`**/api/v1/projects/${p.id}/standard-recommendations`,r=>{
    if(r.request().method()==='GET') return r.fulfill({json:{data:[]}});
    const body=r.request().postDataJSON();expect(body.scope).toBe('image');calls++;
    return r.fulfill({json:{data:{id:'fixed-generation',scope:'image',status:'succeeded',snapshot:{sourceVersionId:'fixed'},result:{report:{rules:[{text:'封面展示品牌A产品',criterion:'封面产品主体完整、品牌A标识清晰可辨。',category:'图文画面',importance:'must',origin:'ai',sourceBlockId:'',sourceQuote:''}],warnings:[]}}}}});
  });
  await page.goto(`/projects/${p.id}/rules?type=script`);
  await expect(page.getByRole('region',{name:'AI 生成脚本规则'})).toBeVisible();
  const original=await page.locator('.script-rule-row strong').first().innerText();
  await page.getByRole('button',{name:'视频规则',exact:true}).click();
  await expect(page.getByRole('region',{name:'AI 生成视频规则'})).toBeVisible();
  await expect(page.getByLabel('AI生成规则严格程度')).toHaveCount(0);
  await page.getByRole('button',{name:'图文规则',exact:true}).click();
  await expect(page.getByRole('region',{name:'AI 生成图文规则'})).toBeVisible();
  await expect(page.getByLabel('AI生成规则严格程度')).toBeVisible();
  await page.getByRole('button',{name:'AI 生成规则',exact:true}).click();
  await expect(page.getByLabel('图文第1条要求',{exact:true})).toHaveValue('封面展示品牌A产品');
  expect(calls).toBe(1);
  await page.getByRole('button',{name:'脚本规则',exact:true}).click();
  await expect(page.locator('.script-rule-row strong').first()).toHaveText(original);
  await page.getByRole('button',{name:'图文规则',exact:true}).click();
  await page.screenshot({path:'test-results/image-generation-card.png',fullPage:true});
});

test('图文生成没有适用新增项时保留原草稿，不报生成失败也不重调',async({page,request})=>{
  const p=(await(await request.post('/api/v1/projects',{headers:{'Idempotency-Key':crypto.randomUUID()},data:{name:'平台范围过滤',brandName:'品牌A',platforms:['xiaohongshu']}})).json()).data;
  let calls=0;
  await page.route('**/api/v1/settings/models/decomposition',r=>r.fulfill({json:{data:{hasKey:true,revision:1}}}));
  await page.route(`**/api/v1/projects/${p.id}/brief`,r=>r.fulfill({json:{data:{brief:{title:'固定Brief',sourceVersionId:'fixed'}}}}));
  await page.route(`**/api/v1/projects/${p.id}/standard-recommendations`,r=>{
    if(r.request().method()==='GET')return r.fulfill({json:{data:[]}});
    calls++;expect(r.request().postDataJSON().scope).toBe('image');
    return r.fulfill({json:{data:{id:'fixed',scope:'image',status:'succeeded',snapshot:{sourceVersionId:'fixed'},result:{report:{rules:[],warnings:['已略过其他交付阶段的要求。']}}}}});
  });
  await page.goto(`/projects/${p.id}/rules?type=image`);
  const first=page.getByLabel('图文第1条要求',{exact:true});
  const before=await first.inputValue();
  await page.getByRole('button',{name:'AI 生成规则',exact:true}).click();
  await expect(page.getByText('本次没有新增适用的图文要求，现有规则保留',{exact:true})).toBeVisible();
  await expect(first).toHaveValue(before);
  expect(calls).toBe(1);
});
