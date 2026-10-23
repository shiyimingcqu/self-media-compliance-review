import { createContext, useContext } from 'react';
export type DemoRole = 'brand' | 'creator';
export const DemoRoleContext = createContext<DemoRole>('brand');
export const useDemoRole = () => useContext(DemoRoleContext);
