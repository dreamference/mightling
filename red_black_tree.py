"""
Red-Black Tree Implementation in Python

A self-balancing binary search tree where each node has an extra bit for color (red or black).
Maintains balance through rotations and color changes during insertions and deletions.

Properties:
1. Every node is either red or black
2. Root is always black
3. All leaves (NIL) are black
4. If a node is red, both its children are black
5. Every path from a node to its descendant leaves has the same black count
"""

from enum import Enum
from typing import Optional, List, Any


class Color(Enum):
    RED = 0
    BLACK = 1


class RBNode:
    """Node for Red-Black Tree."""
    
    def __init__(self, value: Any, color: Color = Color.RED):
        self.value = value
        self.color = color
        self.left: Optional['RBNode'] = None
        self.right: Optional['RBNode'] = None
        self.parent: Optional['RBNode'] = None
    
    def __repr__(self):
        return f"RBNode({self.value}, {'R' if self.color == Color.RED else 'B'})"


class RedBlackTree:
    """
    Red-Black Tree implementation with O(log n) operations.
    
    Supports: insert, delete, search, and various traversal methods.
    """
    
    def __init__(self):
        self.NIL = RBNode(None, Color.BLACK)  # Sentinel NIL node
        self.root: Optional[RBNode] = self.NIL
    
    def _is_red(self, node: Optional[RBNode]) -> bool:
        """Check if a node is red."""
        return node is not None and node.color == Color.RED
    
    def _rotate_left(self, x: RBNode):
        """Perform left rotation on node x."""
        y = x.right
        if y is None:
            return
        
        # Turn y's left subtree into x's right subtree
        x.right = y.left
        if y.left is not None and y.left is not self.NIL:
            y.left.parent = x
        
        # Link x's parent to y
        y.parent = x.parent
        if x.parent is None:
            self.root = y
        elif x == x.parent.left:
            x.parent.left = y
        else:
            x.parent.right = y
        
        # Put x on y's left
        y.left = x
        x.parent = y
    
    def _rotate_right(self, x: RBNode):
        """Perform right rotation on node x."""
        y = x.left
        if y is None:
            return
        
        # Turn y's right subtree into x's left subtree
        x.left = y.right
        if y.right is not None and y.right is not self.NIL:
            y.right.parent = x
        
        # Link x's parent to y
        y.parent = x.parent
        if x.parent is None:
            self.root = y
        elif x == x.parent.right:
            x.parent.right = y
        else:
            x.parent.left = y
        
        # Put x on y's right
        y.right = x
        x.parent = y
    
    def insert(self, value: Any):
        """
        Insert a value into the tree.
        
        Time complexity: O(log n)
        """
        # Standard BST insert
        new_node = RBNode(value, Color.RED)
        new_node.left = self.NIL
        new_node.right = self.NIL
        
        y: Optional[RBNode] = None
        x: Optional[RBNode] = self.root
        
        # Find insertion position
        while x is not None and x is not self.NIL:
            y = x
            if new_node.value < x.value:
                x = x.left
            else:
                x = x.right
        
        # Set new_node's parent
        new_node.parent = y
        
        if y is None:
            # Tree was empty
            self.root = new_node
        elif new_node.value < y.value:
            y.left = new_node
        else:
            y.right = new_node
        
        # Fix Red-Black properties
        self._fix_insert(new_node)
    
    def _fix_insert(self, k: RBNode):
        """Fix Red-Black properties after insertion."""
        while k != self.root and self._is_red(k.parent):
            if k.parent == k.parent.parent.left:
                # Parent is left child
                uncle = k.parent.parent.right
                
                if self._is_red(uncle):
                    # Case 1: Uncle is red - recolor
                    k.parent.color = Color.BLACK
                    uncle.color = Color.BLACK
                    k.parent.parent.color = Color.RED
                    k = k.parent.parent
                else:
                    # Case 2: Uncle is black
                    if k == k.parent.right:
                        k = k.parent
                        self._rotate_left(k)
                    
                    # Case 3: Recolor and rotate
                    k.parent.color = Color.BLACK
                    k.parent.parent.color = Color.RED
                    self._rotate_right(k.parent.parent)
            else:
                # Parent is right child (mirror of above)
                uncle = k.parent.parent.left
                
                if self._is_red(uncle):
                    # Case 1: Uncle is red - recolor
                    k.parent.color = Color.BLACK
                    uncle.color = Color.BLACK
                    k.parent.parent.color = Color.RED
                    k = k.parent.parent
                else:
                    # Case 2: Uncle is black
                    if k == k.parent.left:
                        k = k.parent
                        self._rotate_right(k)
                    
                    # Case 3: Recolor and rotate
                    k.parent.color = Color.BLACK
                    k.parent.parent.color = Color.RED
                    self._rotate_left(k.parent.parent)
        
        self.root.color = Color.BLACK
    
    def delete(self, value: Any) -> bool:
        """
        Delete a value from the tree.
        
        Returns True if deletion was successful, False if value not found.
        Time complexity: O(log n)
        """
        z = self._search_node(value)
        if z is None or z is self.NIL:
            return False
        
        y = z
        y_original_color = y.color
        
        if z.left is self.NIL:
            x = z.right
            self._transplant(z, z.right)
        elif z.right is self.NIL:
            x = z.left
            self._transplant(z, z.left)
        else:
            # z has two children
            y = self._minimum(z.right)
            y_original_color = y.color
            x = y.right
            
            if y.parent == z:
                x.parent = y
            else:
                self._transplant(y, y.right)
                y.right = z.right
                y.right.parent = y
            
            self._transplant(z, y)
            y.left = z.left
            y.left.parent = y
            y.color = z.color
        
        if y_original_color == Color.BLACK:
            self._fix_delete(x)
        
        return True
    
    def _search_node(self, value: Any) -> Optional[RBNode]:
        """Find a node with the given value."""
        node = self.root
        while node is not None and node is not self.NIL:
            if value == node.value:
                return node
            elif value < node.value:
                node = node.left
            else:
                node = node.right
        return self.NIL
    
    def search(self, value: Any) -> bool:
        """Check if a value exists in the tree."""
        return self._search_node(value) is not None and self._search_node(value) is not self.NIL
    
    def _transplant(self, u: RBNode, v: RBNode):
        """Replace subtree rooted at u with subtree rooted at v."""
        if u.parent is None:
            self.root = v
        elif u == u.parent.left:
            u.parent.left = v
        else:
            u.parent.right = v
        v.parent = u.parent
    
    def _minimum(self, node: RBNode) -> RBNode:
        """Find the minimum node in a subtree."""
        while node.left is not None and node.left is not self.NIL:
            node = node.left
        return node
    
    def _maximum(self, node: RBNode) -> RBNode:
        """Find the maximum node in a subtree."""
        while node.right is not None and node.right is not self.NIL:
            node = node.right
        return node
    
    def _fix_delete(self, x: RBNode):
        """Fix Red-Black properties after deletion."""
        while x != self.root and not self._is_red(x):
            if x == x.parent.left:
                w = x.parent.right
                
                if self._is_red(w):
                    # Case 1: Sibling is red
                    w.color = Color.BLACK
                    x.parent.color = Color.RED
                    self._rotate_left(x.parent)
                    w = x.parent.right
                
                if not self._is_red(w.left) and not self._is_red(w.right):
                    # Case 2: Both children of sibling are black
                    w.color = Color.RED
                    x = x.parent
                else:
                    if not self._is_red(w.right):
                        # Case 3: Right child of sibling is black
                        w.left.color = Color.BLACK
                        w.color = Color.RED
                        self._rotate_right(w)
                        w = x.parent.right
                    
                    # Case 4: Right child of sibling is red
                    w.color = x.parent.color
                    x.parent.color = Color.BLACK
                    w.right.color = Color.BLACK
                    self._rotate_left(x.parent)
                    x = self.root
            else:
                # Mirror of above
                w = x.parent.left
                
                if self._is_red(w):
                    w.color = Color.BLACK
                    x.parent.color = Color.RED
                    self._rotate_right(x.parent)
                    w = x.parent.left
                
                if not self._is_red(w.right) and not self._is_red(w.left):
                    w.color = Color.RED
                    x = x.parent
                else:
                    if not self._is_red(w.left):
                        w.right.color = Color.BLACK
                        w.color = Color.RED
                        self._rotate_left(w)
                        w = x.parent.left
                    
                    w.color = x.parent.color
                    x.parent.color = Color.BLACK
                    w.left.color = Color.BLACK
                    self._rotate_right(x.parent)
                    x = self.root
        
        x.color = Color.BLACK
    
    def inorder(self) -> List[Any]:
        """Return values in inorder traversal."""
        result = []
        self._inorder_helper(self.root, result)
        return result
    
    def _inorder_helper(self, node: Optional[RBNode], result: List[Any]):
        if node is not None and node is not self.NIL:
            self._inorder_helper(node.left, result)
            result.append(node.value)
            self._inorder_helper(node.right, result)
    
    def preorder(self) -> List[Any]:
        """Return values in preorder traversal."""
        result = []
        self._preorder_helper(self.root, result)
        return result
    
    def _preorder_helper(self, node: Optional[RBNode], result: List[Any]):
        if node is not None and node is not self.NIL:
            result.append(node.value)
            self._preorder_helper(node.left, result)
            self._preorder_helper(node.right, result)
    
    def postorder(self) -> List[Any]:
        """Return values in postorder traversal."""
        result = []
        self._postorder_helper(self.root, result)
        return result
    
    def _postorder_helper(self, node: Optional[RBNode], result: List[Any]):
        if node is not None and node is not self.NIL:
            self._postorder_helper(node.left, result)
            self._postorder_helper(node.right, result)
            result.append(node.value)
    
    def get_min(self) -> Optional[Any]:
        """Return minimum value in tree."""
        if self.root is self.NIL:
            return None
        return self._minimum(self.root).value
    
    def get_max(self) -> Optional[Any]:
        """Return maximum value in tree."""
        if self.root is self.NIL:
            return None
        return self._maximum(self.root).value
    
    def is_empty(self) -> bool:
        """Check if tree is empty."""
        return self.root is self.NIL
    
    def __len__(self) -> int:
        """Return number of nodes in tree."""
        return self._count_nodes(self.root)
    
    def _count_nodes(self, node: Optional[RBNode]) -> int:
        if node is None or node is self.NIL:
            return 0
        return 1 + self._count_nodes(node.left) + self._count_nodes(node.right)
    
    def __contains__(self, value: Any) -> bool:
        """Check if value is in tree using 'in' operator."""
        return self.search(value)
    
    def __repr__(self):
        return f"RedBlackTree({self.inorder()})"


# Example usage and testing
if __name__ == "__main__":
    print("Red-Black Tree Implementation Tests")
    print("=" * 50)
    
    # Test 1: Basic insertions
    print("\nTest 1: Basic Insertions")
    rb_tree = RedBlackTree()
    values = [10, 20, 30, 15, 25, 5, 1]
    for val in values:
        rb_tree.insert(val)
        print(f"  Inserted {val}: {rb_tree}")
    
    # Test 2: Search
    print("\nTest 2: Search Operations")
    print(f"  Search 15: {rb_tree.search(15)}")
    print(f"  Search 100: {rb_tree.search(100)}")
    print(f"  '20' in tree: {20 in rb_tree}")
    
    # Test 3: Traversals
    print("\nTest 3: Tree Traversals")
    print(f"  Inorder:    {rb_tree.inorder()}")
    print(f"  Preorder:   {rb_tree.preorder()}")
    print(f"  Postorder:  {rb_tree.postorder()}")
    
    # Test 4: Min/Max
    print("\nTest 4: Min/Max")
    print(f"  Min: {rb_tree.get_min()}")
    print(f"  Max: {rb_tree.get_max()}")
    
    # Test 5: Deletions
    print("\nTest 5: Deletions")
    rb_tree2 = RedBlackTree()
    for val in [50, 30, 70, 20, 40, 60, 80]:
        rb_tree2.insert(val)
    print(f"  Before delete: {rb_tree2}")
    
    rb_tree2.delete(20)
    print(f"  After delete 20: {rb_tree2}")
    
    rb_tree2.delete(30)
    print(f"  After delete 30: {rb_tree2}")
    
    rb_tree2.delete(50)
    print(f"  After delete 50: {rb_tree2}")
    
    # Test 6: Empty tree operations
    print("\nTest 6: Empty Tree Operations")
    empty_tree = RedBlackTree()
    print(f"  Is empty: {empty_tree.is_empty()}")
    print(f"  Length: {len(empty_tree)}")
    
    # Test 7: Large dataset
    print("\nTest 7: Large Dataset (100 elements)")
    import random
    rb_tree3 = RedBlackTree()
    random.seed(42)
    large_values = [random.randint(1, 1000) for _ in range(100)]
    for val in large_values:
        rb_tree3.insert(val)
    print(f"  Inserted 100 random values")
    print(f"  Tree length: {len(rb_tree3)}")
    print(f"  Min: {rb_tree3.get_min()}, Max: {rb_tree3.get_max()}")
    print(f"  Sorted (first 10): {rb_tree3.inorder()[:10]}")
    
    # Verify sorting
    sorted_values = rb_tree3.inorder()
    is_sorted = all(sorted_values[i] <= sorted_values[i+1] for i in range(len(sorted_values)-1))
    print(f"  Tree is properly sorted: {is_sorted}")
    
    print("\n" + "=" * 50)
    print("All tests completed!")
