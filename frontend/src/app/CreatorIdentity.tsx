import { createContext, useContext } from 'react';

/** 达人端当前以谁的身份在看：顶部右侧可以切换，选择结果存在本地。 */
export type CreatorIdentity = { creators: string[]; creator: string; setCreator: (name: string) => void };
export const CreatorIdentityContext = createContext<CreatorIdentity>({ creators: [], creator: '', setCreator: () => undefined });
export const useCreatorIdentity = () => useContext(CreatorIdentityContext);