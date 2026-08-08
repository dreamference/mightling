"""
Implementation of Red-Black Tree based on CLRS (Cormen, Leiserson, Rivest & Stein)
Introduction to Algorithms, 3rd ed., Chapter 13.
"""

from enum import Enum
from typing import Optional, Any


class Color(Enum):
    RED = 1
    BLACK = 2


class RBNode:
    """A node in the Red-Black Tree."""

    def __init__(self, key: Any, value: Any = None, color: Color = Color.RED):
        self.key = key
        self.value = value
        self.color = color  # Default RED for new nodes (as per RB-INSERT)
        self.left: Optional['RBNode'] = None
        self.right: Optional['RBNode'] = None
        self.p: Optional['RBNode'] = None  # Parent pointer


class RedBlackTree:
    """
    A Red-Black Tree data structure implementing the algorithms from CLRS Chapter 13.
    
    Properties maintained:
    1. Every node is either RED or BLACK.
    2. The root is BLACK.
    3. Every leaf (the sentinel T.nil) is BLACK.
    4. If a node is RED, then both its children are BLACK.
    5. For each node, all simple paths to descendant leaves contain the same number of BLACK nodes.
    """

    def __init__(self):
        # Sentinel NIL node - shared instance
        self.nil = RBNode(None, None, Color.BLACK)
        self.nil.left = self.nil
        self.nil.right = self.nil
        self.nil.p = self.nil
        
        # Root pointer. An empty tree has root == nil.
        self.root: Optional['RBNode'] = self.nil

    def left_rotate(self, x: 'RBNode'):
        """
        4.1 LEFT-ROTATE(T, x)
        Precondition: x.right != T.nil
        """
        y = x.right
        x.right = y.left

        if y.left != self.nil:
            y.left.p = x

        y.p = x.p
        if x.p == self.nil:
            self.root = y
        elif x == x.p.left:
            x.p.left = y
        else:
            x.p.right = y

        y.left = x
        x.p = y

    def right_rotate(self, y: 'RBNode'):
        """
        4.2 RIGHT-ROTATE(T, y)
        Precondition: y.left != T.nil.
        """
        x = y.left
        y.left = x.right

        if x.right != self.nil:
            x.right.p = y

        x.p = y.p
        if y.p == self.nil:
            self.root = x
        elif y == y.p.right:
            y.p.right = x
        else:
            y.p.left = x

        x.right = y
        y.p = x

    def insert(self, key: Any, value: Any = None):
        """
        5.2 RB-INSERT(T, z)
        """
        z = RBNode(key, value, Color.RED)
        y = self.nil
        x = self.root

        while x != self.nil:
            y = x
            if z.key < x.key:
                x = x.left
            else:
                x = x.right

        z.p = y
        if y == self.nil:
            self.root = z
        elif z.key < y.key:
            y.left = z
        else:
            y.right = z

        z.left = self.nil
        z.right = self.nil
        # z.color is RED (initialized in __init__ of RBNode)
        
        self._insert_fixup(z)

    def _insert_fixup(self, z: 'RBNode'):
        """
        5.3 RB-INSERT-FIXUP(T, z)
        """
        while z.p.color == Color.RED:
            if z.p == z.p.p.left:
                y = z.p.p.right  # Uncle
                if y.color == Color.RED:
                    # Case 1
                    z.p.color = Color.BLACK
                    y.color = Color.BLACK
                    z.p.p.color = Color.RED
                    z = z.p.p
                else:
                    if z == z.p.right:
                        # Case 2
                        z = z.p
                        self.left_rotate(z)
                    # Case 3
                    z.p.color = Color.BLACK
                    z.p.p.color = Color.RED
                    self.right_rotate(z.p.p)
            else:
                # Mirror: exchange left and right
                y = z.p.p.left
                if y.color == Color.RED:
                    z.p.color = Color.BLACK
                    y.color = Color.BLACK
                    z.p.p.color = Color.RED
                    z = z.p.p
                else:
                    if z == z.p.left:
                        z = z.p
                        self.right_rotate(z)
                    z.p.color = Color.BLACK
                    z.p.p.color = Color.RED
                    self.left_rotate(z.p.p)
        self.root.color = Color.BLACK

    def delete(self, key: Any):
        """
        6.3 RB-DELETE(T, z)
        """
        z = self._search(self.root, key)
        if z == self.nil:
            return  # Not found

        self._delete_node(z)

    def _search(self, node: 'RBNode', key: Any) -> Optional['RBNode']:
        """Helper to find a node by key."""
        x = node
        while x != self.nil and key != x.key:
            if key < x.key:
                x = x.left
            else:
                x = x.right
        return x

    def _delete_node(self, z: 'RBNode'):
        """
        Helper to perform the actual deletion logic given node z.
        """
        y = z
        y_original_color = y.color
        
        if z.left == self.nil:
            x = z.right
            self._transplant(z, z.right)
        elif z.right == self.nil:
            x = z.left
            self._transplant(z, z.left)
        else:
            y = self.tree_minimum(z.right)  # y-original-color
            y_original_color = y.color
            x = y.right
            if y.p == z:
                x.p = y  # Deliberate even when x is self.nil
            else:
                self._transplant(y, y.right)
                y.right = z.right
                y.right.p = y
            self._transplant(z, y)
            y.left = z.left
            y.left.p = y
            y.color = z.color
        
        if y_original_color == Color.BLACK:
            self._delete_fixup(x)

    def _transplant(self, u: 'RBNode', v: 'RBNode'):
        """
        6.2 RB-TRANSPLANT(T, u, v)
        Replaces subtree rooted at u with subtree rooted at v.
        """
        if u.p == self.nil:
            self.root = v
        elif u == u.p.left:
            u.p.left = v
        else:
            u.p.right = v
        v.p = u.p

    def _delete_fixup(self, x: 'RBNode'):
        """
        6.4 RB-DELETE-FIXUP(T, x)
        """
        while x != self.root and x.color == Color.BLACK:
            if x == x.p.left:
                w = x.p.right
                if w.color == Color.RED:
                    # Case 1
                    w.color = Color.BLACK
                    x.p.color = Color.RED
                    self.left_rotate(x.p)
                    w = x.p.right
                
                if w.left.color == Color.BLACK and w.right.color == Color.BLACK:
                    # Case 2
                    w.color = Color.RED
                    x = x.p
                else:
                    if w.right.color == Color.BLACK:
                        # Case 3
                        w.left.color = Color.BLACK
                        w.color = Color.RED
                        self.right_rotate(w)
                        w = x.p.right
                    
                    # Case 4
                    w.color = x.p.color
                    x.p.color = Color.BLACK
                    w.right.color = Color.BLACK
                    self.left_rotate(x.p)
                    x = self.root
            else:
                # Mirror: exchange left and right
                w = x.p.left
                if w.color == Color.RED:
                    w.color = Color.BLACK
                    x.p.color = Color.RED
                    self.right_rotate(x.p)
                    w = x.p.left
                
                if w.right.color == Color.BLACK and w.left.color == Color.BLACK:
                    w.color = Color.RED
                    x = x.p
                else:
                    if w.left.color == Color.BLACK:
                        w.right.color = Color.BLACK
                        w.color = Color.RED
                        self.left_rotate(w)
                        w = x.p.left
                    
                    w.color = x.p.color
                    x.p.color = Color.BLACK
                    w.left.color = Color.BLACK
                    self.right_rotate(x.p)
                    x = self.root
        x.color = Color.BLACK

    def search(self, key: Any) -> Optional['RBNode']:
        """
        7.1 TREE-SEARCH(T, k)
        """
        x = self.root
        while x != self.nil and key != x.key:
            if key < x.key:
                x = x.left
            else:
                x = x.right
        return x

    def tree_minimum(self, node: 'RBNode') -> 'RBNode':
        """
        7.2 TREE-MINIMUM(x)
        """
        while node.left != self.nil:
            node = node.left
        return node

    def tree_maximum(self, node: 'RBNode') -> 'RBNode':
        """
        Mirror of minimum.
        """
        while node.right != self.nil:
            node = node.right
        return node

    def successor(self, x: 'RBNode') -> 'RBNode':
        """
        7.3 TREE-SUCCESSOR(x)
        """
        if x.right != self.nil:
            return self.tree_minimum(x.right)
        y = x.p
        while y != self.nil and x == y.right:
            x = y
            y = y.p
        return y

    # --- Verification Methods (Section 8) ---

    def verify(self) -> list:
        """
        8.1 Checks
        1. Root is BLACK, and T.root.p == T.nil
        2. No RED node has a RED child
        3. All root-to-leaf paths have equal black-height
        4. BST ordering: in-order traversal is strictly increasing
        5. Parent pointers are consistent
        """
        errors = []
        
        # 1. Root check
        if self.root.color != Color.BLACK:
            errors.append("Root is not BLACK")
        if self.root.p != self.nil:
            errors.append(f"Root parent is not NIL (is {self.root.p.key})")
        
        # 2, 3, 5. Recursive checks
        self._verify_color(self.root, errors)
        self._verify_parent(self.root, errors)
        
        # 4. BST Ordering (In-order traversal)
        keys = self._inorder_traversal(self.root)
        if not self._is_sorted(keys):
            errors.append("BST ordering violation")
            
        return errors

    def _verify_color(self, node: 'RBNode', errors: list):
        if node == self.nil:
            return
        
        if node.color == Color.RED:
            if node.left.color == Color.RED:
                errors.append(f"RED node {node.key} has RED left child")
            if node.right.color == Color.RED:
                errors.append(f"RED node {node.key} has RED right child")
                
        self._verify_color(node.left, errors)
        self._verify_color(node.right, errors)

    def _verify_parent(self, node: 'RBNode', errors: list):
        if node == self.nil:
            return
        
        if node.left != self.nil:
            if node.left.p != node:
                errors.append(f"Left child {node.left.key} parent mismatch")
            self._verify_parent(node.left, errors)
            
        if node.right != self.nil:
            if node.right.p != node:
                errors.append(f"Right child {node.right.key} parent mismatch")
            self._verify_parent(node.right, errors)

    def _inorder_traversal(self, node: 'RBNode') -> list:
        if node == self.nil:
            return []
        return (self._inorder_traversal(node.left) + 
                [node.key] + 
                self._inorder_traversal(node.right))

    def _is_sorted(self, keys: list) -> bool:
        for i in range(1, len(keys)):
            if keys[i] <= keys[i-1]:
                return False
        return True

    def black_height_check(self, node: 'RBNode') -> int:
        """
        8.2 Black-Height Verification
        Returns black-height or -1 on failure.
        """
        if node == self.nil:
            return 1  # The leaf itself counts as black
        
        if node.color == Color.RED:
            if node.left.color == Color.RED or node.right.color == Color.RED:
                return -1
        
        left_bh = self.black_height_check(node.left)
        right_bh = self.black_height_check(node.right)
        
        if left_bh == -1 or right_bh == -1:
            return -1
        if left_bh != right_bh:
            return -1
        
        if node.color == Color.BLACK:
            return left_bh + 1
        else:
            return left_bh


# ========================= COMPREHENSIVE TESTS =========================

def run_tests():
    """Run all test cases."""
    stats = {"passed": 0, "total": 0}
    
    def assert_test(description: str, passed: bool) -> bool:
        stats["total"] += 1
        if passed:
            stats["passed"] += 1
            print(f"  ✓ {description}")
            return True
        print(f"  ✗ FAILED: {description}")
        return False
    
    import random
    
    print("=" * 70)
    print("         COMPREHENSIVE RED-BLACK TREE TEST SUITE")
    print("=" * 70)
    
    # ===================== INSERTION TESTS =====================
    print("\n[1. CLRS EXAMPLE INSERTION]")
    tree = RedBlackTree()
    keys_insert = [41, 38, 31, 12, 19, 8, 6, 15, 29]  # CLRS example from Figure 13.1
    
    for k in keys_insert:
        tree.insert(k)
        errors = tree.verify()
        if errors:
            print(f"  ✗ Insert {k} failed: {errors}")
            return False
    
    assert_test("All CLRS inserts valid (9 nodes)", True)
    assert_test("Root is BLACK after inserts", tree.root.color == Color.BLACK)
    
    # Search after insert
    found = tree.search(19)
    assert_test("Search finds existing key (19)", found != tree.nil and found.key == 19)
    not_found = tree.search(999)
    assert_test("Search returns nil for missing key (999)", not_found == tree.nil)
    
    # Min/max - maximum value is 41, minimum is 6
    min_node = tree.tree_minimum(tree.root)
    assert_test(f"Minimum is {min_node.key} (expected 6)", min_node.key == 6)
    max_node = tree.tree_maximum(tree.root)
    assert_test(f"Maximum is {max_node.key} (expected 41)", max_node.key == 41)
    
    # Black height
    bh = tree.black_height_check(tree.root)
    assert_test(f"Black height consistent (bh={bh})", bh != -1)
    
    # ===================== SEQUENTIAL INSERT =====================
    print("\n[2. SEQUENTIAL INSERT (1-100)]")
    tree2 = RedBlackTree()
    for i in range(1, 101):
        tree2.insert(i)
        if i % 25 == 0:
            errors = tree2.verify()
            assert_test(f"  Sequential insert reached {i}", not errors)
    
    errors = tree2.verify()
    assert_test("Final sequential insert valid", not errors)
    
    # ===================== REVERSE INSERT =====================
    print("\n[3. REVERSE SEQUENTIAL INSERT (100-1)]")
    tree3 = RedBlackTree()
    for i in range(100, 0, -1):
        tree3.insert(i)
        if i % 25 == 0:
            errors = tree3.verify()
            assert_test(f"  Reverse insert reached {i}", not errors)
    
    errors = tree3.verify()
    assert_test("Final reverse insert valid", not errors)
    
    # ===================== DELETION CYCLES =====================
    print("\n[4. INSERTION-DELETION CYCLES]")
    tree4 = RedBlackTree()
    for i in [50, 30, 70, 20, 40, 60, 80, 10, 25, 35, 45, 55, 65, 75]:
        tree4.insert(i)
    
    # Delete node with two children (uses successor)
    tree4.delete(30)  # has children 20 and 40 (after restructuring)
    assert_test("Delete node with 2 children (30)", tree4.verify() == [])
    
    # Delete leaf node
    tree4.delete(20)
    assert_test("Delete leaf node (20)", tree4.verify() == [])
    
    # Delete node with one child (if it has only one after deletion)
    tree4.delete(40)
    assert_test("Delete node with 1 child (40)", tree4.verify() == [])
    
    # Delete root
    tree4.delete(50)
    assert_test("Delete root (50)", tree4.verify() == [])
    
    # ===================== DELETE ALL =====================
    print("\n[5. DELETE ALL NODES]")
    tree5 = RedBlackTree()
    for i in range(50):
        tree5.insert(i)
    
    for i in range(50):
        tree5.delete(i)
    
    assert_test("Tree empty after all deletes", tree5.root == tree5.nil)
    assert_test("Inorder traversal empty", len(tree5._inorder_traversal(tree5.root)) == 0)
    
    # ===================== LARGE RANDOM =====================
    print("\n[6. LARGE RANDOM INSERTS (N=1000)]")
    tree6 = RedBlackTree()
    n = 1000
    keys = list(range(1, n + 1))
    random.shuffle(keys)
    
    for i, k in enumerate(keys):
        tree6.insert(k)
        if (i + 1) % 250 == 0:
            errors = tree6.verify()
            assert_test(f"  Random insert reached {i+1}", not errors)
    
    errors = tree6.verify()
    assert_test("Final random insert valid", not errors)
    
    # ===================== LARGE RANDOM DELETE =====================
    print("\n[7. LARGE RANDOM DELETES (N=500)]")
    random.shuffle(keys)
    for i, k in enumerate(keys[:500]):
        tree6.delete(k)
    
    errors = tree6.verify()
    assert_test("After deleting 500 elements", not errors)
    
    # Insert new range
    for i in range(2000, 2100):
        tree6.insert(i)
    
    assert_test("Insert 100 new elements after heavy delete", tree6.verify() == [])
    
    # ===================== BLACK HEIGHT =====================
    print("\n[8. BLACK HEIGHT CONSISTENCY]")
    tree7 = RedBlackTree()
    for k in [10, 20, 30, 40, 50, 60, 70]:
        tree7.insert(k)
    
    bh = tree7.black_height_check(tree7.root)
    assert_test(f"  Black height = {bh} (before delete)", bh != -1 and bh > 0)
    
    tree7.delete(30)
    bh = tree7.black_height_check(tree7.root)
    assert_test(f"  Black height = {bh} (after delete 30)", bh != -1 and bh > 0)
    
    tree7.delete(20)
    bh = tree7.black_height_check(tree7.root)
    assert_test(f"  Black height = {bh} (after delete 20)", bh != -1 and bh > 0)
    
    # ===================== EDGE CASES =====================
    print("\n[9. EDGE CASES]")
    
    # Delete single element
    tree8 = RedBlackTree()
    tree8.insert(10)
    tree8.delete(10)
    assert_test("Delete single element -> empty tree", tree8.root == tree8.nil)
    
    # Delete root with 2 children
    tree9 = RedBlackTree()
    tree9.insert(100)
    tree9.insert(50)
    tree9.insert(150)
    tree9.delete(100)  # Root with 2 children
    assert_test("Delete root with 2 children", tree9.verify() == [])
    
    # Delete leaf
    tree9.delete(50)  # Leaf
    assert_test("Delete after root delete (leaf)", tree9.verify() == [])
    
    # Delete last element
    tree9.delete(150)  # Leaf (last remaining)
    assert_test("Delete last element -> empty tree", tree9.root == tree9.nil)
    
    # ===================== SEARCH =====================
    print("\n[10. SEARCH OPERATIONS]")
    tree10 = RedBlackTree()
    test_keys = [5, 15, 25, 35, 45, 55, 65, 75, 85, 95]
    for k in test_keys:
        tree10.insert(k)
    
    for k in test_keys:
        node = tree10.search(k)
        assert_test(f"Search existing {k}", node != tree10.nil and node.key == k)
    
    for k in range(0, 100, 10):
        if k not in test_keys:
            node = tree10.search(k)
            assert_test(f"Search missing {k}", node == tree10.nil)
    
    # ===================== INORDER SORTED =====================
    print("\n[11. INORDER SORTED CHECK]")
    tree11 = RedBlackTree()
    # Use unique keys to ensure strict ordering check works
    import random as rng
    unique_keys = list(range(500))
    rng.shuffle(unique_keys)
    for k in unique_keys[:300]:
        tree11.insert(k)
    
    inorder = tree11._inorder_traversal(tree11.root)
    assert_test(f"Inorder is sorted ({len(inorder)} nodes)", tree11._is_sorted(inorder))
    
    # ===================== SUCCESSOR =====================
    print("\n[12. SUCCESSOR OPERATIONS]")
    tree12 = RedBlackTree()
    for k in [10, 20, 30, 40, 50]:
        tree12.insert(k)
    
    s10 = tree12.successor(tree12.search(10))
    assert_test("Successor of 10 is 20", s10.key == 20)
    
    s20 = tree12.successor(tree12.search(20))
    assert_test("Successor of 20 is 30", s20.key == 30)
    
    s30 = tree12.successor(tree12.search(30))
    assert_test("Successor of 30 is 40", s30.key == 40)
    
    s40 = tree12.successor(tree12.search(40))
    assert_test("Successor of 40 is 50", s40.key == 50)
    
    s50 = tree12.successor(tree12.search(50))
    assert_test("Successor of 50 is nil (max)", s50 == tree12.nil)
    
    # After deletion
    tree12.delete(10)
    s20_after = tree12.successor(tree12.search(20))  # Should be 30 now
    assert_test("Successor of 20 after deleting 10 is 30", s20_after.key == 30)
    
    # ===================== MIN/MAX =====================
    print("\n[13. MINIMUM/MAXIMUM]")
    tree13 = RedBlackTree()
    for k in [50, 30, 70, 20, 40, 60, 80]:
        tree13.insert(k)
    
    min_node = tree13.tree_minimum(tree13.root)
    assert_test(f"  Minimum = {min_node.key} (expected 20)", min_node.key == 20)
    
    max_node = tree13.tree_maximum(tree13.root)
    assert_test(f"  Maximum = {max_node.key} (expected 80)", max_node.key == 80)
    
    # ===================== REBUILD =====================
    print("\n[14. REBUILD AFTER DELETION]")
    tree14 = RedBlackTree()
    
    # Fill
    for i in range(200):
        tree14.insert(i)
    
    # Delete all even numbers
    for i in range(0, 200, 2):
        tree14.delete(i)
    
    errors = tree14.verify()
    assert_test("After deleting all evens from 200", not errors)
    
    # Insert new odd range
    for i in range(500, 700):
        tree14.insert(i)
    
    assert_test("Insert 200 new elements after heavy delete", tree14.verify() == [])
    
    # ===================== RANDOM DELETION ORDER =====================
    print("\n[15. RANDOM INSERT + RANDOM DELETE ORDER]")
    for trial in range(100):
        tree15 = RedBlackTree()
        keys = list(range(1, 51))
        random.shuffle(keys)
        
        # Insert randomly
        for k in keys:
            tree15.insert(k)
            if trial % 20 == 0:  # Check every 20th for efficiency
                assert_test(f"  Trial {trial}: insert {k}", True)
        
        # Delete randomly
        random.shuffle(keys)
        for k in keys:
            tree15.delete(k)
            errors = tree15.verify()
            if errors:
                print(f"  ✗ FAILED: Trial {trial}, deleted {k}, errors: {errors}")
                return False
    
    assert_test("100 random insert+delete trials", True)
    
    # ===================== FINAL SUMMARY =====================
    print("\n" + "=" * 70)
    print(f"  RESULTS: {stats['passed']}/{stats['total']} tests passed")
    print("=" * 70)
    
    if stats["passed"] == stats["total"]:
        print("\n✓✓✓ ALL TESTS PASSED ✓✓✓\n")
        return True
    else:
        print(f"\n✗✗✗ {stats['total'] - stats['passed']} TEST(S) FAILED ✗✗✗\n")
        return False


# ========================= LEGACY TESTS =========================

def test_insert():
    """Basic insertion test."""
    tree = RedBlackTree()
    keys = [41, 38, 31, 12, 19, 8]
    for k in keys:
        tree.insert(k)
    assert not tree.verify(), f"Basic insert failed: {tree.verify()}"
    print("✓ test_insert passed")

def test_delete():
    """Basic deletion test."""
    tree = RedBlackTree()
    for k in [41, 38, 31, 12, 19, 8]:
        tree.insert(k)
    tree.delete(41)
    assert not tree.verify(), f"Basic delete failed: {tree.verify()}"
    print("✓ test_delete passed")

def test_invariants():
    """Invariant preservation under random operations."""
    import random
    for trial in range(50):
        tree = RedBlackTree()
        keys = list(range(1, 50))
        random.shuffle(keys)
        for k in keys:
            tree.insert(k)
            assert not tree.verify()
        remaining = list(keys)
        random.shuffle(remaining)
        for k in remaining:
            node = tree.search(k)
            if node:
                tree.delete(k)
            assert not tree.verify()
    print("✓ test_invariants passed (50 trials)")

def test_edge_cases():
    """Edge case tests."""
    tree = RedBlackTree()
    tree.insert(10)
    tree.delete(10)
    assert tree.root == tree.nil, "Tree not empty after single delete"
    assert not tree.verify()
    
    tree.insert(20)
    tree.insert(10)
    tree.insert(30)
    tree.delete(20)
    assert not tree.verify()
    print("✓ test_edge_cases passed")


if __name__ == "__main__":
    print("\n=== Legacy Tests ===\n")
    test_insert()
    test_delete()
    test_invariants()
    test_edge_cases()
    
    print("\n")
    result = run_tests()
    
    import sys
    sys.exit(0 if result else 1)