import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PublicationFlow from './PublicationFlow';

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('IntersectionObserver', class {
    constructor(private callback: IntersectionObserverCallback) {}
    observe() { this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver); }
    disconnect() {}
  });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('walks through a valid release and stops on the published result', () => {
  render(<PublicationFlow animated />);
  expect(screen.getByText('Illustrative example · not live status')).toBeTruthy();
  for (let i = 0; i < 4; i++) act(() => { vi.advanceTimersByTime(4500); });
  expect(screen.getByRole('heading', { name: 'One release. Every surface.' })).toBeTruthy();
  expect(screen.getByText('New snapshot published')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Replay walkthrough' })).toBeTruthy();
});

it('holds a failed release and never advances to publication', () => {
  render(<PublicationFlow animated />);
  fireEvent.click(screen.getByRole('button', { name: 'Stale quote' }));
  for (let i = 0; i < 5; i++) act(() => { vi.advanceTimersByTime(4500); });
  expect(screen.getByText('Publication blocked')).toBeTruthy();
  expect(screen.getByText('Last validated release stays available')).toBeTruthy();
  expect(screen.queryByText('New snapshot published')).toBeNull();
});

it('allows inspection without auto-advancing and respects reduced motion', () => {
  const { rerender } = render(<PublicationFlow animated={false} />);
  act(() => { vi.advanceTimersByTime(20000); });
  expect(screen.getByRole('heading', { name: 'Start with a timestamp.' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /03 Verify/ }));
  expect(screen.getByRole('heading', { name: 'Make the numbers agree.' })).toBeTruthy();
  rerender(<PublicationFlow animated />);
  act(() => { vi.advanceTimersByTime(20000); });
  expect(screen.getByRole('heading', { name: 'Make the numbers agree.' })).toBeTruthy();
});

it('pauses playback and restarts cleanly after a failed scenario', () => {
  render(<PublicationFlow animated />);
  fireEvent.click(screen.getByRole('button', { name: 'Pause walkthrough' }));
  act(() => { vi.advanceTimersByTime(20000); });
  expect(screen.getByRole('heading', { name: 'Start with a timestamp.' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Stale quote' }));
  act(() => { vi.advanceTimersByTime(4500); });
  fireEvent.click(screen.getByRole('button', { name: 'Valid snapshot' }));
  expect(screen.queryByText('Publication blocked')).toBeNull();
  expect(screen.getByRole('heading', { name: 'Start with a timestamp.' })).toBeTruthy();
});

it('gives a restarted scenario a full interval at the first checkpoint', () => {
  render(<PublicationFlow animated />);
  act(() => { vi.advanceTimersByTime(4400); });
  fireEvent.click(screen.getByRole('button', { name: 'Stale quote' }));
  act(() => { vi.advanceTimersByTime(200); });
  expect(screen.getByRole('heading', { name: 'Start with a timestamp.' })).toBeTruthy();
  act(() => { vi.advanceTimersByTime(4300); });
  expect(screen.getByText('Publication blocked', { exact: true })).toBeTruthy();
});
