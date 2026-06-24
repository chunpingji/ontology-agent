'use client'

import React from 'react'
import * as AccordionPrimitive from '@radix-ui/react-accordion'
import { ChevronRight } from 'lucide-react'
import { cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const treeVariants = cva(
    'group relative hover:before:opacity-100 before:absolute before:rounded-lg before:left-0 px-2 before:w-full before:opacity-0 before:bg-accent/70 before:h-[2rem] before:-z-10'
)

// 选中态高亮：直接给行底色（不再依赖 -z-10 的 before，避免被卡片层叠遮住），
// 蓝色淡填充 + 内描边 + 中等字重，与关系面板聚焦行的视觉一致、清晰可辨。
const selectedTreeVariants = cva(
    'rounded-md bg-primary/10 font-medium text-foreground ring-1 ring-inset ring-primary/30'
)

const dragOverVariants = cva(
    'before:opacity-100 before:bg-primary/20 text-primary-foreground'
)

interface TreeDataItem {
    id: string
    name: string
    icon?: React.ComponentType<{ className?: string }>
    selectedIcon?: React.ComponentType<{ className?: string }>
    openIcon?: React.ComponentType<{ className?: string }>
    children?: TreeDataItem[]
    actions?: React.ReactNode
    onClick?: () => void
    draggable?: boolean
    droppable?: boolean
    disabled?: boolean
    className?: string
}

type TreeRenderItemParams = {
    item: TreeDataItem
    level: number
    isLeaf: boolean
    isSelected: boolean
    isOpen?: boolean
    hasChildren: boolean
}

type TreeProps = React.HTMLAttributes<HTMLDivElement> & {
    data: TreeDataItem[] | TreeDataItem
    initialSelectedItemId?: string
    onSelectChange?: (item: TreeDataItem | undefined) => void
    expandAll?: boolean
    defaultNodeIcon?: React.ComponentType<{ className?: string }>
    defaultLeafIcon?: React.ComponentType<{ className?: string }>
    onDocumentDrag?: (sourceItem: TreeDataItem, targetItem: TreeDataItem) => void
    renderItem?: (params: TreeRenderItemParams) => React.ReactNode
}

// 在树中查找 targetId 的路径（含自身），返回 [rootId, …, targetId]；未找到返回 null。
// 外部选中（图谱点击）时据此展开沿途祖先，使被选节点在树里可见。
function findPath(
    items: TreeDataItem[] | TreeDataItem,
    targetId: string,
    trail: string[] = []
): string[] | null {
    const arr = Array.isArray(items) ? items : [items]
    for (const it of arr) {
        const next = [...trail, it.id]
        if (it.id === targetId) return next
        if (it.children) {
            const found = findPath(it.children, targetId, next)
            if (found) return found
        }
    }
    return null
}

const TreeView = React.forwardRef<HTMLDivElement, TreeProps>(
    (
        {
            data,
            initialSelectedItemId,
            onSelectChange,
            expandAll,
            defaultLeafIcon,
            defaultNodeIcon,
            className,
            onDocumentDrag,
            renderItem,
            ...props
        },
        ref
    ) => {
        const [selectedItemId, setSelectedItemId] = React.useState<
            string | undefined
        >(initialSelectedItemId)

        const [draggedItem, setDraggedItem] = React.useState<TreeDataItem | null>(null)

        const handleSelectChange = React.useCallback(
            (item: TreeDataItem | undefined) => {
                setSelectedItemId(item?.id)
                if (onSelectChange) {
                    onSelectChange(item)
                }
            },
            [onSelectChange]
        )

        const handleDragStart = React.useCallback((item: TreeDataItem) => {
            setDraggedItem(item)
        }, [])

        const handleDrop = React.useCallback((targetItem: TreeDataItem) => {
            if (draggedItem && onDocumentDrag && draggedItem.id !== targetItem.id) {
                onDocumentDrag(draggedItem, targetItem)
            }
            setDraggedItem(null)
        }, [draggedItem, onDocumentDrag])

        const expandedItemIds = React.useMemo(() => {
            if (!initialSelectedItemId) {
                return [] as string[]
            }

            const ids: string[] = []

            function walkTreeItems(
                items: TreeDataItem[] | TreeDataItem,
                targetId: string
            ) {
                if (Array.isArray(items)) {
                    for (let i = 0; i < items.length; i++) {
                        ids.push(items[i].id)
                        if (walkTreeItems(items[i], targetId) && !expandAll) {
                            return true
                        }
                        if (!expandAll) ids.pop()
                    }
                } else if (!expandAll && items.id === targetId) {
                    return true
                } else if (items.children) {
                    return walkTreeItems(items.children, targetId)
                }
            }

            walkTreeItems(data, initialSelectedItemId)
            return ids
        }, [data, expandAll, initialSelectedItemId])

        // 外部选中态（图谱点击节点/边、+新建、保存定位）同步进内部高亮：该组件原本只在
        // 挂载时读 initialSelectedItemId。用「渲染期对比上一次 prop」模式同步，避免 effect 级联渲染。
        const [prevInitialId, setPrevInitialId] = React.useState(initialSelectedItemId)
        if (initialSelectedItemId !== prevInitialId) {
            setPrevInitialId(initialSelectedItemId)
            setSelectedItemId(initialSelectedItemId)
        }

        // 当前选中节点的祖先路径（含自身）：外部选中时据此展开沿途节点，使其可见。
        const selectedPathIds = React.useMemo(
            () => (selectedItemId ? findPath(data, selectedItemId) ?? [] : []),
            [data, selectedItemId]
        )

        return (
            <div className={cn('overflow-hidden relative p-2', className)}>
                <TreeItem
                    data={data}
                    ref={ref}
                    selectedItemId={selectedItemId}
                    handleSelectChange={handleSelectChange}
                    expandedItemIds={expandedItemIds}
                    selectedPathIds={selectedPathIds}
                    defaultLeafIcon={defaultLeafIcon}
                    defaultNodeIcon={defaultNodeIcon}
                    handleDragStart={handleDragStart}
                    handleDrop={handleDrop}
                    draggedItem={draggedItem}
                    renderItem={renderItem}
                    level={0}
                    {...props}
                />
                <div
                    className='w-full h-[48px]'
                    onDrop={() => { handleDrop({id: '', name: 'parent_div'})}}>
                </div>
            </div>
        )
    }
)
TreeView.displayName = 'TreeView'

type TreeItemProps = TreeProps & {
    selectedItemId?: string
    handleSelectChange: (item: TreeDataItem | undefined) => void
    expandedItemIds: string[]
    selectedPathIds?: string[]
    defaultNodeIcon?: React.ComponentType<{ className?: string }>
    defaultLeafIcon?: React.ComponentType<{ className?: string }>
    handleDragStart?: (item: TreeDataItem) => void
    handleDrop?: (item: TreeDataItem) => void
    draggedItem: TreeDataItem | null
    level?: number
}

const TreeItem = React.forwardRef<HTMLDivElement, TreeItemProps>(
    (
        {
            className,
            data,
            selectedItemId,
            handleSelectChange,
            expandedItemIds,
            selectedPathIds,
            defaultNodeIcon,
            defaultLeafIcon,
            handleDragStart,
            handleDrop,
            draggedItem,
            renderItem,
            level,
            onSelectChange,
            expandAll,
            initialSelectedItemId,
            onDocumentDrag,
            ...props
        },
        ref
    ) => {
        if (!(Array.isArray(data))) {
            data = [data]
        }
        return (
            <div ref={ref} role="tree" className={className} {...props}>
                <ul>
                    {data.map((item) => (
                        <li key={item.id}>
                            {item.children ? (
                                <TreeNode
                                    item={item}
                                    level={level ?? 0}
                                    selectedItemId={selectedItemId}
                                    expandedItemIds={expandedItemIds}
                                    selectedPathIds={selectedPathIds}
                                    handleSelectChange={handleSelectChange}
                                    defaultNodeIcon={defaultNodeIcon}
                                    defaultLeafIcon={defaultLeafIcon}
                                    handleDragStart={handleDragStart}
                                    handleDrop={handleDrop}
                                    draggedItem={draggedItem}
                                    renderItem={renderItem}
                                />
                            ) : (
                                <TreeLeaf
                                    item={item}
                                    level={level ?? 0}
                                    selectedItemId={selectedItemId}
                                    handleSelectChange={handleSelectChange}
                                    defaultLeafIcon={defaultLeafIcon}
                                    handleDragStart={handleDragStart}
                                    handleDrop={handleDrop}
                                    draggedItem={draggedItem}
                                    renderItem={renderItem}
                                />
                            )}
                        </li>
                    ))}
                </ul>
            </div>
        )
    }
)
TreeItem.displayName = 'TreeItem'

const TreeNode = ({
    item,
    handleSelectChange,
    expandedItemIds,
    selectedPathIds,
    selectedItemId,
    defaultNodeIcon,
    defaultLeafIcon,
    handleDragStart,
    handleDrop,
    draggedItem,
    renderItem,
    level = 0,
}: {
    item: TreeDataItem
    handleSelectChange: (item: TreeDataItem | undefined) => void
    expandedItemIds: string[]
    selectedPathIds?: string[]
    selectedItemId?: string
    defaultNodeIcon?: React.ComponentType<{ className?: string }>
    defaultLeafIcon?: React.ComponentType<{ className?: string }>
    handleDragStart?: (item: TreeDataItem) => void
    handleDrop?: (item: TreeDataItem) => void
    draggedItem: TreeDataItem | null
    renderItem?: (params: TreeRenderItemParams) => React.ReactNode
    level?: number
}) => {
    // 本节点是否落在外部选中节点的祖先链上（含自身）：决定挂载时是否预展开、
    // 以及外部选中切换时是否需要逐层展开露出被选节点。
    const inSelectedPath = selectedPathIds?.includes(item.id) ?? false
    const [value, setValue] = React.useState(
        expandedItemIds.includes(item.id) || inSelectedPath ? [item.id] : []
    )
    const [isDragOver, setIsDragOver] = React.useState(false)
    const triggerRef = React.useRef<HTMLButtonElement | null>(null)
    const hasChildren = !!item.children?.length
    const isSelected = selectedItemId === item.id
    const isOpen = value.includes(item.id)

    // 外部选中其后代时展开本节点（沿祖先链逐层打开），使被选节点露出。
    // 用「渲染期对比上一次」模式而非 effect：命中已开则原样返回，避免 set-state-in-effect。
    const [prevInPath, setPrevInPath] = React.useState(inSelectedPath)
    if (inSelectedPath !== prevInPath) {
        setPrevInPath(inSelectedPath)
        if (inSelectedPath) {
            setValue((prev) => (prev.includes(item.id) ? prev : [...prev, item.id]))
        }
    }

    // 本节点被选中时滚动到可见处（就近滚动，优先在树的滚动容器内完成）。
    React.useEffect(() => {
        if (isSelected) triggerRef.current?.scrollIntoView({ block: 'nearest' })
    }, [isSelected])

    const onDragStart = (e: React.DragEvent) => {
        if (!item.draggable) {
            e.preventDefault()
            return
        }
        e.dataTransfer.setData('text/plain', item.id)
        handleDragStart?.(item)
    }

    const onDragOver = (e: React.DragEvent) => {
        if (item.droppable !== false && draggedItem && draggedItem.id !== item.id) {
            e.preventDefault()
            setIsDragOver(true)
        }
    }

    const onDragLeave = () => {
        setIsDragOver(false)
    }

    const onDrop = (e: React.DragEvent) => {
        e.preventDefault()
        setIsDragOver(false)
        handleDrop?.(item)
    }

    return (
        <AccordionPrimitive.Root
            type="multiple"
            value={value}
            onValueChange={(s) => setValue(s)}
        >
            <AccordionPrimitive.Item value={item.id}>
                <AccordionTrigger
                    ref={triggerRef}
                    className={cn(
                        treeVariants(),
                        isSelected && selectedTreeVariants(),
                        isDragOver && dragOverVariants(),
                        item.className
                    )}
                    onClick={() => {
                        handleSelectChange(item)
                        item.onClick?.()
                    }}
                    draggable={!!item.draggable}
                    onDragStart={onDragStart}
                    onDragOver={onDragOver}
                    onDragLeave={onDragLeave}
                    onDrop={onDrop}
                >
                    {renderItem ? (
                        renderItem({
                            item,
                            level,
                            isLeaf: false,
                            isSelected,
                            isOpen,
                            hasChildren,
                        })
                    ) : (
                        <>
                            <TreeIcon
                                item={item}
                                isSelected={isSelected}
                                isOpen={isOpen}
                                default={defaultNodeIcon}
                            />
                            <span className="text-sm truncate">{item.name}</span>
                            <TreeActions isSelected={isSelected}>
                                {item.actions}
                            </TreeActions>
                        </>
                    )}
                </AccordionTrigger>
                <AccordionContent className="ml-4 pl-1 border-l">
                    <TreeItem
                        data={item.children ? item.children : item}
                        selectedItemId={selectedItemId}
                        handleSelectChange={handleSelectChange}
                        expandedItemIds={expandedItemIds}
                        selectedPathIds={selectedPathIds}
                        defaultLeafIcon={defaultLeafIcon}
                        defaultNodeIcon={defaultNodeIcon}
                        handleDragStart={handleDragStart}
                        handleDrop={handleDrop}
                        draggedItem={draggedItem}
                        renderItem={renderItem}
                        level={level + 1}
                    />
                </AccordionContent>
            </AccordionPrimitive.Item>
        </AccordionPrimitive.Root>
    )
}

const TreeLeaf = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement> & {
        item: TreeDataItem
        level: number
        selectedItemId?: string
        handleSelectChange: (item: TreeDataItem | undefined) => void
        defaultLeafIcon?: React.ComponentType<{ className?: string }>
        handleDragStart?: (item: TreeDataItem) => void
        handleDrop?: (item: TreeDataItem) => void
        draggedItem: TreeDataItem | null
        renderItem?: (params: TreeRenderItemParams) => React.ReactNode
    }
>(
    (
        {
            className,
            item,
            level,
            selectedItemId,
            handleSelectChange,
            defaultLeafIcon,
            handleDragStart,
            handleDrop,
            draggedItem,
            renderItem,
            ...props
        },
        ref
    ) => {
        const [isDragOver, setIsDragOver] = React.useState(false)
        const isSelected = selectedItemId === item.id
        // 合并外部转发 ref 与本地 ref：本地 ref 用于选中时滚动到可见处。
        const rowRef = React.useRef<HTMLDivElement | null>(null)
        const setRefs = React.useCallback(
            (el: HTMLDivElement | null) => {
                rowRef.current = el
                if (typeof ref === 'function') ref(el)
                else if (ref) ref.current = el
            },
            [ref]
        )
        React.useEffect(() => {
            if (isSelected) rowRef.current?.scrollIntoView({ block: 'nearest' })
        }, [isSelected])

        const onDragStart = (e: React.DragEvent) => {
            if (!item.draggable || item.disabled) {
                e.preventDefault()
                return
            }
            e.dataTransfer.setData('text/plain', item.id)
            handleDragStart?.(item)
        }

        const onDragOver = (e: React.DragEvent) => {
            if (item.droppable !== false && !item.disabled && draggedItem && draggedItem.id !== item.id) {
                e.preventDefault()
                setIsDragOver(true)
            }
        }

        const onDragLeave = () => {
            setIsDragOver(false)
        }

        const onDrop = (e: React.DragEvent) => {
            if (item.disabled) return
            e.preventDefault()
            setIsDragOver(false)
            handleDrop?.(item)
        }

        return (
            <div
                ref={setRefs}
                className={cn(
                    'ml-5 flex text-left items-center py-2 cursor-pointer before:right-1',
                    treeVariants(),
                    className,
                    isSelected && selectedTreeVariants(),
                    isDragOver && dragOverVariants(),
                    item.disabled && 'opacity-50 cursor-not-allowed pointer-events-none',
                    item.className
                )}
                onClick={() => {
                    if (item.disabled) return
                    handleSelectChange(item)
                    item.onClick?.()
                }}
                draggable={!!item.draggable && !item.disabled}
                onDragStart={onDragStart}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
                {...props}
            >
                {renderItem ? (
                    <>
                        <div className="h-4 w-4 shrink-0 mr-1" />
                        {renderItem({
                            item,
                            level,
                            isLeaf: true,
                            isSelected,
                            hasChildren: false,
                        })}
                    </>
                ) : (
                    <>
                        <TreeIcon
                            item={item}
                            isSelected={isSelected}
                            default={defaultLeafIcon}
                        />
                        <span className="flex-grow text-sm truncate">{item.name}</span>
                        <TreeActions isSelected={isSelected && !item.disabled}>
                            {item.actions}
                        </TreeActions>
                    </>
                )}
            </div>
        )
    }
)
TreeLeaf.displayName = 'TreeLeaf'

const AccordionTrigger = React.forwardRef<
    React.ElementRef<typeof AccordionPrimitive.Trigger>,
    React.ComponentPropsWithoutRef<typeof AccordionPrimitive.Trigger>
>(({ className, children, ...props }, ref) => (
    <AccordionPrimitive.Header>
        <AccordionPrimitive.Trigger
            ref={ref}
            className={cn(
                'flex flex-1 w-full items-center py-2 text-left transition-all first:[&[data-state=open]>svg]:first-of-type:rotate-90',
                className
            )}
            {...props}
        >
            <ChevronRight className="h-4 w-4 shrink-0 transition-transform duration-200 text-accent-foreground/50 mr-1" />
            {children}
        </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
))
AccordionTrigger.displayName = AccordionPrimitive.Trigger.displayName

const AccordionContent = React.forwardRef<
    React.ElementRef<typeof AccordionPrimitive.Content>,
    React.ComponentPropsWithoutRef<typeof AccordionPrimitive.Content>
>(({ className, children, ...props }, ref) => (
    <AccordionPrimitive.Content
        ref={ref}
        className={cn(
            'overflow-hidden text-sm transition-all data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down',
            className
        )}
        {...props}
    >
        <div className="pb-1 pt-0">{children}</div>
    </AccordionPrimitive.Content>
))
AccordionContent.displayName = AccordionPrimitive.Content.displayName

const TreeIcon = ({
    item,
    isOpen,
    isSelected,
    default: defaultIcon
}: {
    item: TreeDataItem
    isOpen?: boolean
    isSelected?: boolean
    default?: React.ComponentType<{ className?: string }>
}) => {
    let Icon: React.ComponentType<{ className?: string }> | undefined = defaultIcon
    if (isSelected && item.selectedIcon) {
        Icon = item.selectedIcon
    } else if (isOpen && item.openIcon) {
        Icon = item.openIcon
    } else if (item.icon) {
        Icon = item.icon
    }
    return Icon ? (
        <Icon className="h-4 w-4 shrink-0 mr-2" />
    ) : (
        <></>
    )
}

const TreeActions = ({
    children,
    isSelected
}: {
    children: React.ReactNode
    isSelected: boolean
}) => {
    return (
        <div
            className={cn(
                isSelected ? 'block' : 'hidden',
                'absolute right-3 group-hover:block'
            )}
        >
            {children}
        </div>
    )
}

export { 
    TreeView, 
    type TreeDataItem, 
    type TreeRenderItemParams,
    AccordionTrigger,
    AccordionContent,
    TreeLeaf,
    TreeNode,
    TreeItem
}
