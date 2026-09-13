/// <reference types="./types" />
declare class Editor {
    element: HTMLTextAreaElement;
    composingLock: boolean;
    processTimeoutId: number;
    constructor(vditor: IVditor);
    private bindEvent;
}
export { Editor };
