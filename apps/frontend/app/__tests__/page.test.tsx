import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import Home from '../page';

describe('Home Page', () => {
  it('renders the heading', () => {
    render(<Home />);
    expect(screen.getByText('Quantiv')).toBeDefined();
  });

  it('renders the subtitle', () => {
    render(<Home />);
    expect(screen.getByText('Weekly expected moves for upcoming earnings')).toBeDefined();
  });
});
