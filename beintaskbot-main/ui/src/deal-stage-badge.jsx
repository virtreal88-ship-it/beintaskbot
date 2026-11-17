import React from 'react';
export function DealStageBadge({name, appearance}) {
  return <span data-deal-stage-badge="" title={name} style={{display:'inline-block',alignSelf:'flex-start',maxWidth:'100%',boxSizing:'border-box',borderRadius:9999,padding:'4px 10px',fontSize:12,lineHeight:'20px',fontWeight:700,whiteSpace:'normal',overflowWrap:'anywhere',background:appearance.background,color:appearance.ink}}>{name}</span>;
}

export function DealStageSelect({appearance, children, compact=false, ...props}) {
  const [focused,setFocused]=React.useState(false);
  if(compact){
    const selected=React.Children.toArray(children).find(child=>String(child.props?.value)===String(props.value));
    return <span data-compact-stage-pill="" style={{position:'relative',display:'inline-flex',alignItems:'center',maxWidth:'100%',boxSizing:'border-box',height:28,borderRadius:9999,padding:'4px 10px',fontSize:12,lineHeight:'20px',fontWeight:700,background:appearance.background,color:appearance.ink,outline:focused?'2px solid currentColor':undefined,outlineOffset:2}}>
      <span aria-hidden="true" style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',pointerEvents:'none'}}>{selected?.props.children}</span>
      <span aria-hidden="true" style={{fontSize:10,marginLeft:6,pointerEvents:'none'}}>⌄</span>
      <select {...props} data-deal-stage-control="" onFocus={()=>setFocused(true)} onBlur={()=>setFocused(false)} style={{position:'absolute',top:-8,left:0,width:'100%',height:44,minHeight:44,opacity:0,cursor:props.disabled?'default':'pointer'}}>{children}</select>
    </span>;
  }
  return <select {...props} data-deal-stage-control="" style={{display:'block',width:'auto',maxWidth:'100%',boxSizing:'border-box',minHeight:compact?28:44,height:compact?28:undefined,border:0,borderRadius:9999,padding:compact?'4px 10px':'6px 14px',fontSize:12,lineHeight:'20px',fontWeight:700,textOverflow:'ellipsis',background:appearance.background,color:appearance.ink,cursor:props.disabled?'default':'pointer'}}>{children}</select>;
}
