import { test, expect } from '@playwright/test';

test('上传Brief后自动处理，刷新跟踪事实卡，规则页等待就绪后自动引用', async ({ page, request }) => {
  const p=(await (await request.post('http://127.0.0.1:8001/api/v1/projects',{headers:{'Idempotency-Key':crypto.randomUUID()},data:{name:'Brief自动处理测试',brandName:'测试品牌',platforms:['douyin']}})).json()).data;
  let uploaded=false,status='vision',uploadCalls=0;
  const brief=()=>({title:'自动Brief.pdf',revision:1,sourceVersionId:'source-auto',fileId:'file-auto',pageCount:1,blocks:status==='succeeded'?[{id:'b',text:'品牌：测试品牌。产品：芝士浓奶铁。',pageNo:1}]:[],uncoveredSources:status==='vision'?['page:1']:[],visualRun:null,processing:{id:'flow',sourceVersionId:'source-auto',status,createdAt:new Date(Date.now()-3000).toISOString(),updatedAt:status==='succeeded'?new Date(Date.now()-1000).toISOString():new Date().toISOString()}});
  await page.route('**/api/v1/files',r=>r.fulfill({json:{data:{id:'file-auto'}}}));
  await page.route('**/api/v1/settings/models',r=>r.fulfill({json:{data:{hasKey:true,revision:1}}}));
  await page.route('**/api/v1/settings/models/decomposition',r=>r.fulfill({json:{data:{hasKey:true,revision:1}}}));
  await page.route(`**/api/v1/projects/${p.id}/brief`,async r=>{
    if(r.request().method()==='POST') {
      expect(r.request().postDataJSON().autoProcess).toBe(true);uploaded=true;uploadCalls++;
      await r.fulfill({json:{data:{revision:1,brief:brief(),registration:{extractionStatus:'ready'}}}});
    } else await r.fulfill({json:{data:{projectId:p.id,revision:uploaded?1:0,brief:uploaded?brief():null}}});
  });
  await page.route(`**/api/v1/projects/${p.id}/brief/facts`,r=>r.fulfill({json:{data:{sourceVersionId:'source-auto',lastRun:status==='succeeded'?{id:'fact-card',status:'succeeded',result:{factsComplete:false,pendingFacts:[{group:'freeform',value:'省略拼接条目',pageNo:1,source:'text',quote:'品牌……产品',reason:'引文无法连续核对'}]}}:null,facts:status==='succeeded'?{brand:{value:'测试品牌',blockId:'b',quote:'品牌：测试品牌。'},products:[{value:'芝士浓奶铁',blockId:'b',quote:'产品：芝士浓奶铁。'}],sellingPoints:[],requiredTerms:[],forbiddenTerms:[],publishRules:[],thresholds:[],freeform:[],warnings:[]}:null}}}));
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`/projects/${p.id}`);
  await page.getByLabel('上传项目Brief').setInputFiles({name:'自动Brief.pdf',mimeType:'application/pdf',buffer:Buffer.from('mock-pdf')});
  await page.getByRole('button',{name:'上传并解析',exact:true}).click();
  await expect(page.getByText('正在解析图文并生成事实卡',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'AI 图文解析',exact:true})).toHaveCount(0);
  await expect(page.locator('.project-brief-summary .ai-elapsed')).toHaveText(/已用时 \d+ 秒/);
  await page.reload();
  await expect(page.getByText('正在解析图文并生成事实卡',{exact:true})).toBeVisible();
  status='facts';await expect(page.getByText('正在自动提取事实卡',{exact:true})).toBeVisible();

  await page.goto(`/projects/${p.id}/rules?type=script`);
  const generate=page.getByRole('button',{name:'AI 生成规则',exact:true});
  await expect(generate).toBeDisabled();
  status='succeeded';await expect(generate).toBeEnabled();
  await expect(page.getByText(/自动引用事实卡/)).toBeVisible();
  await page.goto(`/projects/${p.id}`);
  await expect(page.getByText('事实卡已就绪，规则生成将自动引用',{exact:true})).toBeVisible();
  await expect(page.locator('.project-brief-summary .ai-elapsed')).toHaveText('2 秒完成');
  await expect(page.getByText('芝士浓奶铁',{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'查看解析内容',exact:true}).click();
  await expect(page.getByText('芝士浓奶铁',{exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'待核对 1',exact:true}).click();
  await expect(page.getByText('省略拼接条目',{exact:true})).toBeVisible();
  await page.getByRole('tab',{name:/事实卡/}).click();
  await expect(page.getByText('芝士浓奶铁',{exact:true})).toBeVisible();
  expect(uploadCalls).toBe(1);expect(errors).toEqual([]);
});
