#!/usr/bin/env node
/*
 * mytime.doosan.com "근태계획변경" 자동 신청 스크립트.
 *
 * 사용법:
 *   node src/apply.js --date 2026-07-23 --type 근무 --start 08:30 --end 17:00 [--reason "사유"] [--submit]
 *
 * --submit 을 주지 않으면 화면 입력까지만 하고 "신청" 버튼은 누르지 않는 dry-run 모드로 동작한다.
 * (실제 사이트 접속 없이 만들어진 선택자가 있어 첫 실행은 반드시 dry-run으로 검증할 것 — README 참고)
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const WORK_TYPES = require('./workTypes');

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    if (key === 'submit') {
      args.submit = true;
      continue;
    }
    args[key] = argv[++i];
  }
  return args;
}

function usageAndExit(msg) {
  if (msg) console.error(msg + '\n');
  console.error(
    `사용법: node src/apply.js --date YYYY-MM-DD --type 근무 --start HH:MM --end HH:MM [--reason "사유"] [--submit]\n\n` +
      `  --date    변경할 날짜 (YYYY-MM-DD)\n` +
      `  --type    근무유형 (${Object.keys(WORK_TYPES).join(', ')})\n` +
      `  --start   시작시간 (HH:MM)\n` +
      `  --end     종료시간 (HH:MM)\n` +
      `  --reason  상세사유 (선택, 일부 근무유형은 필수일 수 있음)\n` +
      `  --submit  실제로 "신청" 버튼까지 클릭해서 제출. 생략하면 입력만 하고 제출 직전에 멈춤(dry-run)\n`
  );
  process.exit(1);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.date || !args.type || !args.start || !args.end) usageAndExit('필수 옵션이 누락되었습니다.');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(args.date)) usageAndExit('--date 형식은 YYYY-MM-DD 여야 합니다.');
  if (!/^\d{2}:\d{2}$/.test(args.start) || !/^\d{2}:\d{2}$/.test(args.end)) {
    usageAndExit('--start / --end 형식은 HH:MM 이어야 합니다.');
  }
  const geuntaeCode = WORK_TYPES[args.type];
  if (!geuntaeCode) {
    usageAndExit(`알 수 없는 근무유형: "${args.type}"\n사용 가능한 값: ${Object.keys(WORK_TYPES).join(', ')}`);
  }

  const { DOOSAN_BASE_URL, DOOSAN_AD_ID, DOOSAN_AD_PW } = process.env;
  if (!DOOSAN_AD_ID || !DOOSAN_AD_PW) {
    usageAndExit('환경변수 DOOSAN_AD_ID / DOOSAN_AD_PW 가 설정되어 있지 않습니다. (.env 참고)');
  }
  const baseUrl = DOOSAN_BASE_URL || 'https://mytime.doosan.com/';

  const outDir = path.join(__dirname, '..', 'run-logs', new Date().toISOString().replace(/[:.]/g, '-'));
  fs.mkdirSync(outDir, { recursive: true });

  // 이 sandbox에는 playwright npm 패키지 버전과 무관하게 고정 경로에 Chromium이 미리 설치되어
  // 있으므로(自動 다운로드 불가), 있으면 그 실행파일을 우선 사용한다. 로컬/다른 환경에서는
  // PW_EXECUTABLE_PATH를 비워두면 playwright가 기본 방식으로 브라우저를 찾는다.
  const preinstalledChromium = '/opt/pw-browsers/chromium';
  const executablePath =
    process.env.PW_EXECUTABLE_PATH || (fs.existsSync(preinstalledChromium) ? preinstalledChromium : undefined);
  const browser = await chromium.launch({ headless: process.env.HEADFUL !== '1', executablePath });
  const context = await browser.newContext();
  const page = await context.newPage();

  const shot = async (name) => {
    try {
      await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: true });
    } catch (_) {
      // 스크린샷 실패는 무시 (디버깅 보조 기능일 뿐 핵심 흐름이 아님)
    }
  };

  try {
    console.log(`[1/6] 로그인 페이지 접속: ${baseUrl}`);
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    await shot('01-login-page');

    // TODO(실제 사이트 검증 필요): 로그인 입력창 selector. 캡처상 라벨 없는 text/password 입력 2개.
    await page.locator('input[type="text"]').first().fill(DOOSAN_AD_ID);
    await page.locator('input[type="password"]').first().fill(DOOSAN_AD_PW);
    await page.getByText('Sign in', { exact: false }).click();
    await page.waitForLoadState('networkidle');
    await shot('02-after-login');

    console.log('[2/6] 근태계획변경 메뉴로 이동');
    await page.getByText('근태계획변경').first().click();
    await page.waitForLoadState('networkidle');
    await shot('03-geuntae-list');

    console.log('[3/6] 대상 월로 이동');
    const target = new Date(`${args.date}T00:00:00`);
    const targetLabel = `${target.getFullYear()}년${target.getMonth() + 1}월`;
    const headerLocator = page.locator('text=/\\d{4}년\\s*\\d{1,2}월/').first();
    for (let i = 0; i < 24; i++) {
      const headerText = (await headerLocator.innerText()).replace(/\s/g, '');
      if (headerText === targetLabel) break;
      const m = headerText.match(/(\d{4})년(\d{1,2})월/);
      if (!m) throw new Error(`월 헤더를 해석할 수 없음: "${headerText}"`);
      const curDate = new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, 1);
      const targetFirst = new Date(target.getFullYear(), target.getMonth(), 1);
      // TODO(실제 사이트 검증 필요): 이전/다음 달 화살표 selector. 캡처상 "<" / ">" 텍스트로 추정.
      if (curDate < targetFirst) {
        await page.getByText('>', { exact: true }).first().click();
      } else {
        await page.getByText('<', { exact: true }).first().click();
      }
      await page.waitForTimeout(300);
    }
    await shot('04-target-month');

    console.log(`[4/6] ${args.date} 날짜 선택`);
    const day = String(target.getDate());
    await page.getByText(new RegExp(`^${day}$`)).first().click();
    await page.waitForLoadState('networkidle');
    await shot('05-day-detail');

    console.log('[5/6] 기존 근태 삭제 후 신규 신청 입력');
    // TODO(실제 사이트 검증 필요): "당일 근태 현황" 기존 항목 삭제(X) 버튼 selector.
    const existingClose = page
      .locator('text=당일 근태 현황')
      .locator('xpath=following::*[contains(@class,"close") or contains(@class,"del") or normalize-space(text())="×" or normalize-space(text())="x"]')
      .first();
    if ((await existingClose.count()) > 0) {
      await existingClose.click();
      await page.waitForTimeout(300);
    }

    // TODO(실제 사이트 검증 필요): "추가" 버튼 selector.
    const addButton = page.getByText('추가', { exact: true });
    if ((await addButton.count()) > 0) {
      await addButton.first().click();
      await page.waitForTimeout(300);
    }

    await page.locator('#geuntae_cd').selectOption(geuntaeCode);

    // 시작/종료 시간 input은 readonly 커스텀 타임피커라서 휠 조작 대신
    // DOM 값을 직접 세팅하고 input/change 이벤트를 발생시킨다.
    await page.evaluate(
      ({ start, end }) => {
        const setVal = (id, val) => {
          const el = document.getElementById(id);
          if (!el) throw new Error(`요소를 찾을 수 없음: ${id}`);
          el.value = val;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        };
        setVal('add_sta_time0', start);
        setVal('add_end_time0', end);
      },
      { start: args.start, end: args.end }
    );

    if (args.reason) {
      // TODO(실제 사이트 검증 필요): 상세사유 textarea selector. placeholder 텍스트로 추정.
      const reasonBox = page.getByPlaceholder('근태사유를 입력해주세요.');
      if ((await reasonBox.count()) > 0) await reasonBox.fill(args.reason);
    }

    // TODO(실제 사이트 검증 필요): 입력 행 확정 "선택" 버튼 selector 및 순서.
    const rowConfirm = page.getByText('선택', { exact: true }).last();
    if ((await rowConfirm.count()) > 0) {
      await rowConfirm.click();
      await page.waitForTimeout(300);
    }
    await shot('06-filled');

    console.log(`[6/6] 최종 제출 (${args.submit ? '실행' : 'dry-run: 건너뜀'})`);
    if (args.submit) {
      // TODO(실제 사이트 검증 필요): 최종 "신청" 버튼 selector.
      await page.getByText('신청', { exact: true }).last().click();
      await page.waitForLoadState('networkidle');
      await shot('07-submitted');
      console.log('제출 완료.');
    } else {
      console.log('dry-run 모드입니다. 실제 제출하려면 --submit 옵션을 추가하세요.');
      console.log(`스크린샷 확인: ${outDir}`);
    }
  } catch (err) {
    await shot('99-error');
    console.error('오류 발생:', err.message);
    console.error(`스크린샷 확인: ${outDir}`);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
