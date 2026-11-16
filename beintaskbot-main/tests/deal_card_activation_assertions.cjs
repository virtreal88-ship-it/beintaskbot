const assert=require('node:assert/strict');
module.exports=function(w,card,opened){
 const click=node=>node.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true}));
 let before=opened.length;click(card);assert.equal(opened.length,before+1,'blank card opens');
 const text=card.querySelector('strong,p,span');if(text){before=opened.length;click(text);assert.equal(opened.length,before+1,'card text opens');}
 for(const key of ['Enter',' ']){before=opened.length;card.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true}));assert.equal(opened.length,before+1,'card keyboard opens');}
 for(const tag of ['a','button','input','select','textarea','label']){const child=w.document.createElement(tag);card.append(child);before=opened.length;click(child);child.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));assert.equal(opened.length,before,'embedded controls are not stolen: '+tag);child.remove();}
 before=opened.length;card.dispatchEvent(new w.MouseEvent('pointerdown',{bubbles:true,clientX:10,clientY:10}));card.dispatchEvent(new w.MouseEvent('pointermove',{bubbles:true,clientX:80,clientY:10}));card.dispatchEvent(new w.MouseEvent('pointerup',{bubbles:true}));click(card);assert.equal(opened.length,before,'swipe must not open');
 const drag=new w.Event('dragstart',{bubbles:true,cancelable:true});Object.defineProperty(drag,'dataTransfer',{value:{setData(){}}});card.dispatchEvent(drag);card.dispatchEvent(new w.Event('dragend',{bubbles:true}));click(card);assert.equal(opened.length,before,'drag end must not open');
};
