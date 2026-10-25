import {test,expect} from '@playwright/test';

test('顶部工具移入左下角，搜索、通知、角色与达人切换和手机导航仍可用',async({page})=>{
  await page.setViewportSize({width:1440,height:960});
  await page.addInitScript(()=>localStorage.setItem('review-demo-role','brand'));
  await page.route('**/api/v1/creator/tasks',r=>r.fulfill({json:{data:['达人甲','达人乙'].map((name,index)=>({id:`t${index}`,project_id:`p${index}`,creator_name:name,project_name:`合作${index}`,brand_name:'品牌',platform:'douyin',rulesReady:true,scriptAcceptance:{passed:false,checks:[]},brandApproved:false}))}}));
  await page.route('**/creator-reminders',r=>r.fulfill({json:{data:[]}}));
  await page.goto('/projects');
  const tools=page.getByRole('region',{name:'工作空间操作'});
  await expect(page.locator('.desk-topbar')).toHaveCount(0);
  const box=(await tools.boundingBox())!;expect(box.x+box.width).toBeLessThan(270);expect(box.y).toBeGreaterThan(650);
  await tools.getByRole('button',{name:'全局搜索',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button',{name:'关闭搜索',exact:true}).click();
  await tools.getByRole('button',{name:'通知：查看待审核队列',exact:true}).click();
  await expect(page).toHaveURL(/\/reviews$/);
  await tools.getByRole('button',{name:'切换到达人端',exact:true}).click();
  await expect(page).toHaveURL(/\/creator$/);
  await tools.getByRole('combobox',{name:'切换达人',exact:true}).click();
  await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)').locator('.ant-select-item-option-content').getByText('达人乙',{exact:true}).click();
  await expect(tools.locator('.desk-account-name')).toHaveText('达人乙');
  await tools.getByRole('button',{name:'收起导航',exact:true}).click();
  await expect(page.locator('.desk-shell')).toHaveClass(/is-collapsed/);
  await tools.getByRole('button',{name:'选择达人',exact:true}).click();
  await expect(page.getByRole('combobox',{name:'切换当前达人',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  await tools.getByRole('button',{name:'展开导航',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'切换导航',exact:true}).click();
  await expect(tools.getByRole('button',{name:'全局搜索',exact:true})).toBeVisible();
  expect((await tools.boundingBox())!.x).toBeGreaterThanOrEqual(0);
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

