import React from 'react';
export function safeURL(value: string | null | undefined): string | undefined {try {const u=new URL(value || '');return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:undefined;}catch{return undefined;}}
export function SourceLink({url,children}:{url?:string|null;children:React.ReactNode}) {const href=safeURL(url);return href?<a href={href} rel="noopener noreferrer">{children}</a>:<span>{children} (not publicly inspectable)</span>;}
