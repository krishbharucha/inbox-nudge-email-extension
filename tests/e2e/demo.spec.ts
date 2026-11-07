import { test, expect } from '@playwright/test';

test('sender, recipient, unconnected request, and preferences', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Explore as Alex' }).click();
  await expect(page.getByRole('heading', { name: 'Your inbox, a little lighter.' })).toBeVisible();
  await page.getByRole('button', { name: 'Send a nudge', exact: true }).click();
  const launch = page.locator('.sent-row').filter({ hasText: 'Launch checklist' });
  await launch.getByRole('button', { name: 'Remind recipient' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Eligible for a reminder')).toBeVisible();
  await dialog.getByRole('button', { name: 'Send reminder' }).click();
  await expect(dialog.getByRole('heading', { name: 'Reminder delivered' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Done' }).click();
  await launch.getByRole('button', { name: 'Remind recipient' }).click();
  await expect(dialog.getByText('Reminder cooldown active', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Send reminder' })).toBeDisabled();
  await page.keyboard.press('Escape');

  const recruiter = page.locator('.sent-row').filter({ hasText: 'product designer role' });
  await recruiter.getByRole('button', { name: 'Remind recipient' }).click();
  await expect(dialog.getByText('Recipient not connected', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Record request' }).click();
  await expect(dialog.getByText('DEMO DELIVERY EXPLANATION')).toBeVisible();
  await expect(
    dialog.getByText(
      'Requires separate Gmail send permission and your confirmation. The demo sends nothing.',
    ),
  ).toBeVisible();
  await dialog.getByRole('button', { name: 'Done' }).click();
  await page.getByRole('button', { name: 'Switch to Maya' }).click();
  await page
    .getByRole('navigation', { name: 'Main navigation' })
    .getByRole('button', { name: /Priority inbox/ })
    .click();
  await expect(
    page.getByText('Launch checklist — a few final details', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'View thread' }).click();
  await dialog.getByRole('button', { name: 'Dismiss reminder' }).click();
  await expect(page.getByRole('heading', { name: 'A little breathing room.' })).toBeVisible();
  await page
    .locator('.sidebar-bottom')
    .getByRole('button', { name: 'Preferences', exact: true })
    .click();
  await page.getByRole('switch', { name: 'Allow incoming reminders' }).click();
  await expect(page.getByRole('switch', { name: 'Allow incoming reminders' })).toHaveAttribute(
    'aria-checked',
    'false',
  );
  await page
    .getByRole('textbox', { name: 'Sender email to block' })
    .fill('alex.morgan@example.com');
  await page.getByRole('button', { name: 'Block sender', exact: true }).click();
  await expect(page.locator('.blocked-row')).toContainText('alex.morgan@example.com');
  await page.reload();
  await page
    .locator('.sidebar-bottom')
    .getByRole('button', { name: 'Preferences', exact: true })
    .click();
  await expect(page.getByRole('switch', { name: 'Allow incoming reminders' })).toHaveAttribute(
    'aria-checked',
    'false',
  );
  await expect(page.locator('.blocked-row')).toContainText('alex.morgan@example.com');
});

test('mobile workspace fits and navigation works', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Explore as Alex' }).click();
  await expect(page.getByRole('heading', { name: 'Your inbox, a little lighter.' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('.mobile-nav').getByRole('button', { name: 'Preferences' }).click();
  await expect(page.getByRole('heading', { name: 'Your inbox. Your boundaries.' })).toBeVisible();
});
