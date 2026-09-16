import { describe, expect, it } from 'vitest';
import {
  displayForecastClass,
  displayForecastLabel,
  finiteDisplayForecast,
} from './displayForecast';

describe('display forecast helpers', () => {
  it('uses compact provenance labels', () => {
    expect(displayForecastLabel('ml')).toBe('ML forecast');
    expect(displayForecastLabel('options_math')).toBe('Market implied');
    expect(displayForecastLabel('options_indicative')).toBe('Market implied');
    expect(displayForecastLabel('historical')).toBe('Historical median');
    expect(displayForecastLabel('historical_prior')).toBe('Historical prior');
  });

  it('maps methods onto semantic style classes without badges', () => {
    expect(displayForecastClass('ml')).toContain('qv-forecast--ml');
    expect(displayForecastClass('options_math')).toContain('qv-forecast--options');
    expect(displayForecastClass('options_indicative')).toContain('qv-forecast--indicative');
    expect(displayForecastClass('historical')).toContain('qv-forecast--historical');
    expect(displayForecastClass('historical_prior')).toContain('qv-forecast--prior');
  });

  it('accepts only finite positive display forecasts', () => {
    expect(finiteDisplayForecast(0.04)).toBe(0.04);
    expect(finiteDisplayForecast(0)).toBeNull();
    expect(finiteDisplayForecast(Number.NaN)).toBeNull();
    expect(finiteDisplayForecast(null)).toBeNull();
  });
});
