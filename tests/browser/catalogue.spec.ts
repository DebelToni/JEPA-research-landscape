import {test,expect} from '../../site/node_modules/@playwright/test';
test('catalogue, paper, graph, downloads and methodology',async({page,request})=>{
 await page.goto('/');await expect(page.getByRole('heading',{name:'Research catalogue'})).toBeVisible();
 const response=await request.get('/data/catalog.json');expect(response.ok()).toBeTruthy();const data=await response.json();
 if(data.papers.length){const paper=data.papers[0];await page.getByRole('searchbox').fill(paper.title);await expect(page.getByRole('link',{name:paper.title,exact:true})).toBeVisible();await page.getByRole('link',{name:paper.title,exact:true}).click();await expect(page.getByRole('heading',{name:paper.title,exact:true})).toBeVisible();await expect(page.getByLabel('Local citation graph')).toBeVisible();expect((await request.get(`/data/papers/${paper.work_id}.json`)).ok()).toBeTruthy();await page.reload();await expect(page.getByRole('heading',{name:paper.title,exact:true})).toBeVisible();const refs=page.locator('.refs a');if(await refs.count()){const href=await refs.first().getAttribute('href');await refs.first().click();await expect(page.locator(`[id="${decodeURIComponent(href!.slice(1))}"]`)).toBeInViewport();}}
 await page.getByRole('link',{name:'Methodology',exact:true}).click();await expect(page.getByRole('heading',{name:'Appraisal policy'})).toBeVisible();expect(await page.locator('body').evaluate(el=>el.scrollWidth<=window.innerWidth)).toBeTruthy();
});
test('synthetic contract: null extraction, facets and unsafe evidence',async({page})=>{test.skip(!!process.env.BASE_URL,'Fixture assertions only');await page.goto('/');await page.getByLabel('Category',{exact:true}).selectOption('robotics');await expect(page.getByRole('status')).toHaveText('1 papers');await page.getByRole('link',{name:'Synthetic latent prediction',exact:true}).click();await expect(page.locator('.key-result strong')).toHaveText('0');await expect(page.getByText('Limited to the synthetic setting.')).toBeVisible();await expect(page.locator('.katex')).toBeVisible();await expect(page.locator('.key-result')).toContainText('Named predictor');await expect(page.locator('article')).toContainText('75/100');await expect(page.locator('table')).toHaveCount(0);expect(await page.locator('body').evaluate(el=>el.scrollWidth<=window.innerWidth)).toBeTruthy();await expect(page.locator('article img')).toHaveCount(0);await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);await page.getByRole('link',{name:'[e1]',exact:true}).first().click();await expect(page.locator('#evidence-e1')).toContainText('Table 1');await page.getByRole('link',{name:'[c0:e2]',exact:true}).first().click();await expect(page.locator('[id="evidence-c0:e2"] a')).toHaveAttribute('href','https://example.org/paper/source.tex#L20');await page.goto('/papers/33333333-3333-4333-8333-333333333333/');await expect(page.getByText('Experimental interpretation pending.')).toBeVisible();await expect(page.getByText('Private exhaustive row')).toHaveCount(0);await page.goto('/papers/22222222-2222-4222-8222-222222222222/');await expect(page.getByText('Not yet extracted. No appraisal is available.')).toBeVisible();});

test('cascading categories include descendants, count once and reset children',async({page})=>{
 test.skip(!!process.env.BASE_URL,'Fixture assertions only');
 await page.goto('/');
 const category=page.getByLabel('Category',{exact:true});
 await expect(page.getByLabel('topic',{exact:true})).toHaveCount(0);
 await expect(category.locator('option[value="science"]')).toHaveText('Science (2)');
 await category.selectOption('science');
 await expect(page.getByRole('status')).toHaveText('2 papers');
 await page.getByLabel('Subcategory',{exact:true}).selectOption('science/domains');
 await expect(page.getByRole('status')).toHaveText('2 papers');
 await page.getByLabel('Third level',{exact:true}).selectOption('science/domains/fluids');
 await expect(page.getByRole('status')).toHaveText('1 papers');
 await expect(page.locator('.papers')).toContainText('Science / Scientific domains / Fluids');
 await page.getByLabel('relevance',{exact:true}).selectOption('adjacent');
 await expect(page.getByRole('status')).toHaveText('0 papers');
 await page.getByLabel('relevance',{exact:true}).selectOption('');
 await page.getByLabel('Subcategory',{exact:true}).selectOption('');
 await expect(page.getByLabel('Third level',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('status')).toHaveText('2 papers');
 await category.selectOption('robotics');
 await expect(page.getByLabel('Subcategory',{exact:true})).toHaveValue('');
 await page.getByLabel('Subcategory',{exact:true}).selectOption('robotics/manipulation');
 await expect(page.getByRole('status')).toHaveText('1 papers');
 await category.selectOption('');
 await expect(page.getByLabel('Subcategory',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('status')).toHaveText('3 papers');
 expect(await page.locator('body').evaluate(el=>el.scrollWidth<=window.innerWidth)).toBeTruthy();
});
