class Node:
    def __init__(self, key, color='RED'):
        self.key = key
        self.color = color  # 'RED' or 'BLACK'
        self.left = None
        self.right = None
        self.parent = None

class RedBlackTree:
    def __init__(self):
        # Sentinel NIL node
        self.TNULL = Node(None, 'BLACK')
        self.TNULL.color = 'BLACK'
        self.root = self.TNULL

    def insert(self, key):
        node = Node(key, 'RED')
        node.left = self.TNULL
        node.right = self.TNULL
        self._insert_node(node)

    def _insert_node(self, node):
        curr = self.root
        while curr != self.TNULL:
            node.parent = curr
            if node.key < curr.key:
                curr = curr.left
            else:
                curr = curr.right
        
        if node.parent is None:
            self.root = node
            node.color = 'BLACK'
            return

        if node.key < node.parent.key:
            node.parent.left = node
        else:
            node.parent.right = node

        if node.parent.parent is None:
            return

        self._insert_fixup(node)

    def _insert_fixup(self, node):
        while node.parent != self.TNULL and node.parent.color == 'RED':
            if node.parent == node.parent.parent.left:
                uncle = node.parent.parent.right
                if uncle.color == 'RED':
                    node.parent.color = 'BLACK'
                    uncle.color = 'BLACK'
                    node.parent.parent.color = 'RED'
                    node = node.parent.parent
                else:
                    if node == node.parent.right:
                        node = node.parent
                        self._left_rotate(node)
                    node.parent.color = 'BLACK'
                    node.parent.parent.color = 'RED'
                    self._right_rotate(node.parent.parent)
            else:
                uncle = node.parent.parent.left
                if uncle.color == 'RED':
                    node.parent.color = 'BLACK'
                    uncle.color = 'BLACK'
                    node.parent.parent.color = 'RED'
                    node = node.parent.parent
                else:
                    if node == node.parent.left:
                        node = node.parent
                        self._right_rotate(node)
                    node.parent.color = 'BLACK'
                    node.parent.parent.color = 'RED'
                    self._left_rotate(node.parent.parent)
        
        self.root.color = 'BLACK'

    def delete(self, key):
        self._delete_node(self.root, key)

    def _delete_node(self, node, key):
        if node == self.TNULL:
            return
        
        if key == node.key:
            self._delete_me(node)
        elif key < node.key:
            self._delete_node(node.left, key)
        else:
            self._delete_node(node.right, key)

    def _delete_me(self, node):
        y = node
        y_original_color = y.color
        if node.left == self.TNULL:
            x = node.right
            self._transplant(node, node.right)
            if x != self.TNULL:
                x.parent = node.parent
        elif node.right == self.TNULL:
            x = node.left
            self._transplant(node, node.left)
            if x != self.TNULL:
                x.parent = node.parent
        else:
            y = node.right
            while y.left != self.TNULL:
                y = y.left
            y_original_color = y.color
            x = y.right
            if y.parent == node:
                x.parent = y
            else:
                self._transplant(y, y.right)
                y.right = node.right
                y.right.parent = y
            
            self._transplant(node, y)
            y.left = node.left
            y.left.parent = y
            y.color = node.color
        
        if y_original_color == 'BLACK':
            self._delete_fixup(x)

    def _delete_fixup(self, node):
        while node != self.root and node.color == 'RED':
            if node == node.parent.left:
                sibling = node.parent.right
                if sibling.color == 'RED':
                    sibling.color = 'BLACK'
                    node.parent.color = 'RED'
                    self._left_rotate(node.parent)
                    sibling = node.parent.right

                if sibling.left.color == 'BLACK' and sibling.right.color == 'BLACK':
                    sibling.color = 'RED'
                    node = node.parent
                else:
                    if sibling.right.color == 'BLACK':
                        sibling.left.color = 'BLACK'
                        sibling.color = 'RED'
                        self._right_rotate(sibling)
                        sibling = node.parent.right

                    sibling.color = node.parent.color
                    node.parent.color = 'BLACK'
                    sibling.right.color = 'BLACK'
                    self._left_rotate(node.parent)
                    node = self.root
            else:
                sibling = node.parent.left
                if sibling.color == 'RED':
                    sibling.color = 'BLACK'
                    node.parent.color = 'RED'
                    self._right_rotate(node.parent)
                    sibling = node.parent.left

                if sibling.right.color == 'BLACK' and sibling.left.color == 'BLACK':
                    sibling.color = 'RED'
                    node = node.parent
                else:
                    if sibling.left.color == 'BLACK':
                        sibling.right.color = 'BLACK'
                        sibling.color = 'RED'
                        self._left_rotate(sibling)
                        sibling = node.parent.left

                    sibling.color = node.parent.color
                    node.parent.color = 'BLACK'
                    sibling.left.color = 'BLACK'
                    self._right_rotate(node.parent)
                    node = self.root
            node.color = 'BLACK'

    def _transplant(self, old_node, new_node):
        if old_node.parent == self.TNULL:
            self.root = new_node
        elif old_node == old_node.parent.left:
            old_node.parent.left = new_node
        else:
            old_node.parent.right = new_node
        new_node.parent = old_node.parent

    def _left_rotate(self, x):
        y = x.right
        x.right = y.left
        if y.left != self.TNULL:
            y.left.parent = x
        y.parent = x.parent
        if x.parent == self.TNULL:
            self.root = y
        elif x == x.parent.left:
            x.parent.left = y
        else:
            x.parent.right = y
        y.left = x
        x.parent = y

    def _right_rotate(self, y):
        x = y.left
        y.left = x.right
        if x.right != self.TNULL:
            x.right.parent = y
        x.parent = y.parent
        if y.parent == self.TNULL:
            self.root = x
        elif y == y.parent.right:
            y.parent.right = x
        else:
            y.parent.left = x
        x.right = y
        y.parent = x

    def inorder(self):
        res = []
        self._inorder_helper(self.root, res)
        return res

    def _inorder_helper(self, node, res):
        if node != self.TNULL:
            self._inorder_helper(node.left, res)
            res.append(node.key)
            self._inorder_helper(node.right, res)

# Example usage:
if __name__ == "__main__":
    rbt = RedBlackTree()
    keys = [20, 10, 30, 5, 15, 25, 35, 3, 7, 12, 18, 22, 28, 32, 38]
    for k in keys:
        rbt.insert(k)
    
    print("Inorder Traversal:", rbt.inorder())
    
    rbt.delete(10)
    print("After deleting 10:", rbt.inorder())
