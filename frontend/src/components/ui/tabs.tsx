import * as Primitive from '@radix-ui/react-tabs';
export const Tabs = Primitive.Root;
export const TabsContent = Primitive.Content;
export function TabsList(props: React.ComponentProps<typeof Primitive.List>) { return <Primitive.List className="desk-tabs" {...props} />; }
export function TabsTrigger(props: React.ComponentProps<typeof Primitive.Trigger>) { return <Primitive.Trigger className="desk-tab" {...props} />; }
