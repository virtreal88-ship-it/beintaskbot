import React from 'react';
export function DealStageBadge({name, appearance}) {
  return <span data-deal-stage-badge="" title={name} style={{display:'inline-block',alignSelf:'flex-start',maxWidth:'100%',boxSizing:'border-box',borderRadius:9999,padding:'4px 10px',fontSize:12,lineHeight:'20px',fontWeight:700,whiteSpace:'normal',overflowWrap:'anywhere',background:appearance.background,color:appearance.ink}}>{name}</span>;
}

export function DealStageSelect({appearance, children, ...props}) {
  return <select {...props} data-deal-stage-control="" style={{display:'block',width:'auto',maxWidth:'100%',boxSizing:'border-box',minHeight:44,border:0,borderRadius:9999,padding:'6px 14px',fontSize:12,lineHeight:'20px',fontWeight:700,textOverflow:'ellipsis',background:appearance.background,color:appearance.ink,cursor:props.disabled?'default':'pointer'}}>{children}</select>;
}
