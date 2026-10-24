import {test,expect} from '@playwright/test';

test('脚本审核依次优先展示未通过、待确定、建议、通过，允许手动查看空分类',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('review-demo-role','creator'));
  let mode='uncertain';
  const modes=[{mode:'failed',verdict:'non_compliant',importance:'must',label:'未通过'},{mode:'uncertain',verdict:'needs_confirmation',importance:'must',label:'待确定'},{mode:'suggestions',verdict:'non_compliant',importance:'suggested',label:'建议'},{mode:'passed',verdict:'compliant',importance:'must',label:'通过'}];
  await page.route('**/api/v1/creator/tasks',r=>r.fulfill({json:{data:[{id:'task',project_id:'project',creator_name:'达人',project_name:'默认分类',brand_name:'品牌',rulesReady:true}]}}));
  await page.route('**/api/v1/tasks/task/workflow',r=>{
    const current=modes.find(row=>row.mode===mode)!;
    const review={id:'same-review',status:'succeeded',scriptVersionId:'s1',createdAt:'2026-10-24T10:00:00Z',snapshot:{rules:[{id:'r1',importance:current.importance}]},result:{report:{summary:'固定测试',findings:[{rule_id:'r1',rule_number:1,verdict:current.verdict,quote:'产品',description:'本条审核说明',suggestion:''}]}}};
    return r.fulfill({json:{data:{writable:true,rulesReady:true,currentScript:{scriptVersionId:'s1',versionLabel:'s1',inputType:'text',extractionStatus:'ready',text:'产品',sourceMap:[],parseMeta:{}},scriptReview:review,scriptReviews:[review],scriptAcceptance:{passed:mode==='passed',status:'needs_confirmation',message:'固定测试',checks:[]}}}});
  });
  for(const current of modes){
    mode=current.mode;await page.goto('/creator/projects/project/tasks/task');await page.reload();
    await expect(page.getByRole('radio',{name:`${current.label} 1 项`,exact:true})).toBeChecked();
    await expect(page.locator('.review-comment')).toContainText('本条审核说明');
  }
  await page.getByText('未通过 0 项',{exact:true}).click();
  await expect(page.getByRole('radio',{name:'未通过 0 项',exact:true})).toBeChecked();
  await expect(page.locator('.review-comment')).toHaveCount(0);
});

test('视频审核没有未通过时依次展示待确定、参考建议、已通过',async({page})=>{
  let mode='uncertain';
  const modes=[{mode:'uncertain',verdict:'needs_confirmation',importance:'must',label:'待确定'},{mode:'advisory',verdict:'non_compliant',importance:'suggested',label:'仅参考'},{mode:'passed',verdict:'compliant',importance:'must',label:'已通过'}];
  await page.route('**/api/v1/settings/models',r=>r.fulfill({json:{data:{hasKey:false,revision:1}}}));
  await page.route('**/api/v1/evidence-runs*',r=>{
    expect(r.request().method()).toBe('GET');const current=modes.find(row=>row.mode===mode)!;
    return r.fulfill({json:{data:[{id:'run',kind:'video',status:'succeeded',configRevision:1,createdAt:'2026-10-24T10:00:00Z',snapshot:{rules:[{id:'r1',text:'产品检查',importance:current.importance}]},result:{report:{findings:[{id:'issue1',rule_id:'r1',verdict:current.verdict,description:'本条视频审核说明',evidence_ids:[]}]}}}]}});
  });
  for(const current of modes){
    mode=current.mode;await page.goto('/settings/models?run=run');await page.reload();
    await expect(page.getByRole('tab',{name:new RegExp(current.label)})).toHaveAttribute('data-state','active');
    await expect(page.locator('.finding-card')).toContainText('本条视频审核说明');
  }
});
