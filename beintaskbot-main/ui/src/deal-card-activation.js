import {useRef} from 'react';

const interactive = 'button,a,input,select,textarea,label,[role="button"],[contenteditable="true"]';
// Keep the whole card tappable without stealing embedded controls or opening after a drag.
export function useDealCardActivation(onOpen) {
  const gesture = useRef({point:null, moved:false, until:0});
  return id => ({
    role:onOpen ? 'group' : undefined,
    tabIndex:onOpen ? 0 : undefined,
    onPointerDown(event) {
      gesture.current.point={x:event.clientX,y:event.clientY};
      gesture.current.moved=false;
    },
    onPointerMove(event) {
      const point=gesture.current.point;
      if(point&&Math.hypot(event.clientX-point.x,event.clientY-point.y)>8)gesture.current.moved=true;
    },
    onPointerUp() { if(gesture.current.moved)gesture.current.until=Date.now()+350;gesture.current.point=null; },
    onPointerCancel() { gesture.current.point=null;gesture.current.until=Date.now()+350; },
    onDragStartCapture() {gesture.current.until=Date.now()+350;},
    onDragEndCapture() {gesture.current.until=Date.now()+350;},
    onClick(event) {
      if(!onOpen||event.defaultPrevented||Date.now()<gesture.current.until||event.target.closest(interactive))return;
      onOpen(id);
    },
    onKeyDown(event) {
      if(onOpen&&event.target===event.currentTarget&&['Enter',' '].includes(event.key)) {
        event.preventDefault();onOpen(id);
      }
    },
  });
}
