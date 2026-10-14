import { createContext } from 'react';
import type { UiData } from './types';

export interface UiPresentation {
  data: UiData;
  previous: UiData;
  ageMs: number;
}
export const UiPresentationContext = createContext<UiPresentation | null>(null);
