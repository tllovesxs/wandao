export declare const replaceSelection: (vditor: IVditor, text: string, selectionMode?: SelectionMode) => void;
export declare const processPaste: (vditor: IVditor, text: string) => void;
export declare const processAfterRender: (vditor: IVditor, options?: {
    enableAddUndoStack: boolean;
    enableHint: boolean;
    enableInput: boolean;
}) => void;
export declare const processHeading: (vditor: IVditor, value: string) => void;
export declare const processToolbar: (vditor: IVditor, actionBtn: Element, prefix: string, suffix: string) => void;
